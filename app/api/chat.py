"""HTTP boundary for the legacy public chat contract."""

import base64
import hashlib
import hmac
import logging
import re
import secrets
from typing import Annotated

from fastapi import APIRouter, Cookie, HTTPException, Request, Response
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

from app.agent.graph import RuleIntentClassifier
from app.composition import execute_chat_request as chat
from app.api.auth import OptionalCurrentUser
from app.core.errors import AppError, ERROR_STATUS, safe_error_detail
from app.core.config import get_settings
from app.core.logging import log_subject, operational_context
from app.core.http import normalized_client_network
from app.core.usage import ProviderUnavailable
from app.core.rate_limit import request_rate_limiter
from app.schemas import ChatRequest, ChatResponse, SourceCitation, TravelProfile


router = APIRouter()
_SESSION_COOKIE = "travel_session"
_SESSION_COMPONENT = re.compile(r"^[A-Za-z0-9_-]{43}$")
_DEVELOPMENT_SESSION_SECRET = secrets.token_bytes(32)
_request_rate_limiter = request_rate_limiter
_KNOWN_INTENTS = {
    "plan_trip",
    "modify_trip",
    "explain_trip",
    "smalltalk",
    "unsupported",
}


def _session_signing_secret() -> bytes:
    configured = get_settings().anon_session_signing_secret
    return configured.get_secret_value().encode("utf-8") if configured is not None else _DEVELOPMENT_SESSION_SECRET


def _base64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _sign_session_id(session_id: str) -> str:
    return _base64url(
        hmac.digest(_session_signing_secret(), session_id.encode("ascii"), "sha256")
    )


def _issue_anonymous_session() -> tuple[str, str]:
    session_id = secrets.token_urlsafe(32)
    return session_id, f"{session_id}.{_sign_session_id(session_id)}"


def _verify_anonymous_session(cookie: str | None) -> str | None:
    if not cookie:
        return None
    parts = cookie.split(".")
    if (
        len(parts) != 2
        or not _SESSION_COMPONENT.fullmatch(parts[0])
        or not _SESSION_COMPONENT.fullmatch(parts[1])
    ):
        return None
    session_id, presented_signature = parts
    expected_signature = _sign_session_id(session_id)
    return session_id if hmac.compare_digest(presented_signature, expected_signature) else None


def _anonymous_quota_subject(request: Request) -> str:
    network = normalized_client_network(request.scope)
    digest = hmac.new(
        _session_signing_secret(),
        b"anonymous-quota-v1\0" + network.encode("ascii"),
        hashlib.sha256,
    ).hexdigest()
    return "anon-network:" + digest


def _log_subject(quota_subject: str) -> str:
    if quota_subject.startswith("anon-network:"):
        return quota_subject
    digest = hmac.new(
        _session_signing_secret(),
        b"log-subject-v1\0" + quota_subject.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return "user-digest:" + digest


def _fallback_intent(error: Exception, request_intent: str) -> str:
    claimed = getattr(error, "intent", None)
    return claimed if claimed in _KNOWN_INTENTS else request_intent


def _bounded_citations(sources: list[dict]) -> list[SourceCitation] | None:
    """Keep the first validated citation for each evidence id, then cap output."""
    bounded: list[SourceCitation] = []
    seen_evidence_ids: set[str] = set()
    for source in sources:
        citation = SourceCitation.model_validate(source)
        if citation.evidence_id in seen_evidence_ids:
            continue
        seen_evidence_ids.add(citation.evidence_id)
        bounded.append(citation)
        if len(bounded) == 100:
            break
    return bounded or None


def _json_chat_response(
    response: Response,
    *,
    reply: str,
    stage: str,
    profile: TravelProfile,
    itinerary=None,
    trip_id=None,
    sources: list[dict] | None = None,
    warnings: list[str] | None = None,
) -> JSONResponse:
    """Validate the public model before returning an explicit response object."""
    payload = ChatResponse.model_validate(
        {
            "reply": reply,
            "stage": stage,
            "profile": profile,
            "itinerary": itinerary,
            "trip_id": trip_id,
            "sources": _bounded_citations(sources or []),
            "warnings": (warnings or [])[:40] or None,
        }
    )
    content = payload.model_dump(mode="json")
    for optional_field in ("itinerary", "trip_id", "sources", "warnings"):
        if content[optional_field] is None:
            del content[optional_field]
    return JSONResponse(
        jsonable_encoder(content),
        headers=dict(response.headers),
    )


@router.post("/api/chat", response_model=ChatResponse)
def api_chat(
    request: ChatRequest,
    response: Response,
    user: OptionalCurrentUser,
    http_request: Request,
    anonymous_session: Annotated[str | None, Cookie(alias=_SESSION_COOKIE)] = None,
) -> ChatResponse | JSONResponse:
    settings = get_settings()
    request_intent = RuleIntentClassifier().classify(
        request.message, request.trip_id is not None
    ).intent
    http_request.state.log_intent = request_intent
    if user is not None:
        session_scope = f"user:{user.id}"
        quota_subject = session_scope
    else:
        session_id = _verify_anonymous_session(anonymous_session)
        if session_id is None:
            session_id, signed_cookie = _issue_anonymous_session()
            response.set_cookie(
                _SESSION_COOKIE,
                signed_cookie,
                httponly=True,
                samesite="lax",
                secure=settings.app_env == "production",
                max_age=60 * 60 * 24,
            )
        session_scope = "anon:" + hashlib.sha256(session_id.encode("ascii")).hexdigest()
        quota_subject = _anonymous_quota_subject(http_request)
    safe_log_subject = _log_subject(quota_subject)
    http_request.state.log_subject = safe_log_subject

    subject_limit = (
        settings.request_authenticated_per_minute
        if user is not None
        else settings.request_anonymous_per_minute
    )
    if not _request_rate_limiter.allow(
        (("subject:" + quota_subject, subject_limit),)
    ):
        logging.getLogger("app.api.chat").warning(
            "request_rate_limited",
            extra=operational_context(
                subject=safe_log_subject,
                intent=request_intent,
                stage="rejected",
                error_code="REQUEST_RATE_LIMITED",
            ),
        )
        raise HTTPException(
            status_code=429,
            detail={
                "code": "REQUEST_RATE_LIMITED",
                "message": "Too many requests; please retry later",
            },
        )

    with log_subject(safe_log_subject):
        # Keep only the component boundary, never exception text or request content.
        failure_stage = "application"
        try:
            try:
                result = chat(
                    user,
                    request.trip_id,
                    request.message,
                    thread_id=request.thread_id,
                    session_scope=session_scope,
                    quota_subject=quota_subject,
                    action=request.action,
                )
            except ProviderUnavailable as exc:
                code = exc.code if exc.code in {"AI_RATE_LIMITED", "AI_UNAVAILABLE", "AI_CIRCUIT_OPEN"} else "AI_UNAVAILABLE"
                logging.getLogger("app.api.chat").info(
                    "chat_result",
                    extra=operational_context(
                        intent=_fallback_intent(exc, request_intent),
                        stage="collecting",
                        error_code=code,
                        trip_saved=False,
                    ),
                )
                failure_stage = "response"
                return _json_chat_response(
                    response,
                    reply="AI provider is temporarily unavailable.",
                    stage="collecting",
                    profile=TravelProfile(),
                    warnings=[code],
                )
            if result.error_code == "AGENT_UNAVAILABLE":
                logging.getLogger("app.api.chat").info(
                    "chat_result",
                    extra=operational_context(
                        stage=result.stage,
                        intent=result.intent or request_intent,
                        error_code=result.error_code,
                        trip_saved=False,
                    ),
                )
                failure_stage = "response"
                return _json_chat_response(
                    response,
                    reply=result.reply,
                    stage=result.stage,
                    profile=TravelProfile.model_validate(result.profile),
                    warnings=["AI_PROVIDER_UNAVAILABLE"],
                )
            logging.getLogger("app.api.chat").info(
                "chat_result",
                extra=operational_context(
                    stage=result.stage,
                    intent=result.intent or request_intent,
                    error_code=result.error_code,
                    trip_saved=result.persisted_this_request,
                ),
            )
            failure_stage = "response"
            return _json_chat_response(
                response,
                reply=result.reply,
                stage=result.stage,
                profile=TravelProfile.model_validate(result.profile),
                itinerary=result.itinerary,
                trip_id=result.trip_id,
                sources=result.sources,
                warnings=result.warnings,
            )
        except AppError as exc:
            logging.getLogger("app.api.chat").info(
                "chat_result",
                extra=operational_context(
                    intent=_fallback_intent(exc, request_intent),
                    stage="rejected",
                    error_code=exc.code,
                    trip_saved=False,
                ),
            )
            raise HTTPException(status_code=ERROR_STATUS.get(exc.code, 503), detail=safe_error_detail(exc)) from None
        except ProviderUnavailable as exc:
            code = exc.code if exc.code in {"AI_RATE_LIMITED", "AI_UNAVAILABLE", "AI_CIRCUIT_OPEN"} else "AI_UNAVAILABLE"
            logging.getLogger("app.api.chat").info(
                "chat_result",
                extra=operational_context(
                    intent=_fallback_intent(exc, request_intent),
                    stage="collecting",
                    error_code=code,
                    trip_saved=False,
                ),
            )
            failure_stage = "response"
            return _json_chat_response(
                response,
                reply="AI provider is temporarily unavailable.",
                stage="collecting",
                profile=TravelProfile(),
                warnings=[code],
            )
        except Exception as exc:
            logging.getLogger("app.api.chat").info(
                "chat_result",
                extra=operational_context(
                    intent=_fallback_intent(exc, request_intent),
                    stage="error",
                    error_code="CHAT_UNAVAILABLE",
                    trip_saved=False,
                ),
            )
            logging.getLogger("app.api.chat").warning(
                "chat_request_failed",
                extra=operational_context(
                    error_code="CHAT_UNAVAILABLE",
                    exception_type=type(exc).__name__,
                    failure_stage=failure_stage,
                ),
            )
            raise HTTPException(
                status_code=503,
                detail={"code": "CHAT_UNAVAILABLE", "message": "Chat service is temporarily unavailable"},
            ) from None
