"""HTTP boundary for the legacy public chat contract."""

import base64
import hashlib
import hmac
import ipaddress
import logging
import os
import re
import secrets
from typing import Annotated

from fastapi import APIRouter, Cookie, HTTPException, Request, Response
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

from app.composition import execute_chat_request as chat
from app.api.auth import OptionalCurrentUser
from app.core.errors import AppError, ERROR_STATUS, safe_error_detail
from app.core.config import get_settings
from app.core.logging import log_subject, operational_context
from app.core.usage import ProviderUnavailable
from app.schemas import ChatRequest, ChatResponse, SourceCitation, TravelProfile


router = APIRouter()
_SESSION_COOKIE = "travel_session"
_SESSION_COMPONENT = re.compile(r"^[A-Za-z0-9_-]{43}$")
_DEVELOPMENT_SESSION_SECRET = secrets.token_bytes(32)


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


def _normalized_client_network(request: Request) -> str:
    source = get_settings().trusted_client_ip_header
    if source == "cf-connecting-ip":
        values = request.headers.getlist("CF-Connecting-IP")
        if len(values) != 1 or "," in values[0]:
            return "unavailable"
        candidate = values[0].strip()
    else:
        candidate = request.client.host if request.client is not None else "unavailable"
    try:
        address = ipaddress.ip_address(candidate)
    except ValueError:
        return "unavailable"
    prefix_length = 32 if address.version == 4 else 64
    return str(ipaddress.ip_network(f"{address}/{prefix_length}", strict=False))


def _anonymous_quota_subject(request: Request) -> str:
    network = _normalized_client_network(request)
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
                secure=os.environ.get("APP_ENV", "development").lower() == "production",
                max_age=60 * 60 * 24,
            )
        session_scope = "anon:" + hashlib.sha256(session_id.encode("ascii")).hexdigest()
        quota_subject = _anonymous_quota_subject(http_request)
    safe_log_subject = _log_subject(quota_subject)
    http_request.state.log_subject = safe_log_subject

    with log_subject(safe_log_subject):
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
                return _json_chat_response(
                    response,
                    reply="AI provider is temporarily unavailable.",
                    stage="collecting",
                    profile=TravelProfile(),
                    warnings=[code],
                )
            if result.error_code == "AGENT_UNAVAILABLE":
                return _json_chat_response(
                    response,
                    reply=result.reply,
                    stage=result.stage,
                    profile=TravelProfile.model_validate(result.profile),
                    warnings=["AI_PROVIDER_UNAVAILABLE"],
                )
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
            raise HTTPException(status_code=ERROR_STATUS.get(exc.code, 503), detail=safe_error_detail(exc)) from None
        except ProviderUnavailable as exc:
            code = exc.code if exc.code in {"AI_RATE_LIMITED", "AI_UNAVAILABLE", "AI_CIRCUIT_OPEN"} else "AI_UNAVAILABLE"
            return _json_chat_response(
                response,
                reply="AI provider is temporarily unavailable.",
                stage="collecting",
                profile=TravelProfile(),
                warnings=[code],
            )
        except Exception as exc:
            logging.getLogger("app.api.chat").warning(
                "chat_request_failed",
                extra=operational_context(error_code="CHAT_UNAVAILABLE", exception_type=type(exc).__name__),
            )
            raise HTTPException(
                status_code=503,
                detail={"code": "CHAT_UNAVAILABLE", "message": "Chat service is temporarily unavailable"},
            ) from None
