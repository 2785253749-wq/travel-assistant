"""HTTP boundary for the legacy public chat contract."""

import base64
import hashlib
import hmac
import logging
import os
import re
import secrets
from typing import Annotated

from fastapi import APIRouter, Cookie, HTTPException, Response
from fastapi.responses import JSONResponse

from app.agent.graph import chat
from app.api.auth import OptionalCurrentUser
from app.core.errors import AppError, ERROR_STATUS, safe_error_detail
from app.core.config import get_settings
from app.core.usage import ProviderUnavailable, get_usage_guard, model_usage_scope
from app.schemas import ChatRequest, ChatResponse, TravelProfile


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


@router.post("/api/chat", response_model=ChatResponse)
def api_chat(
    request: ChatRequest,
    response: Response,
    user: OptionalCurrentUser,
    anonymous_session: Annotated[str | None, Cookie(alias=_SESSION_COOKIE)] = None,
) -> ChatResponse | JSONResponse:
    try:
        if user is not None:
            session_scope = f"user:{user.id}"
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
        reservation = get_usage_guard().reserve(session_scope)
        try:
            with model_usage_scope() as model_usage:
                result = chat(user, None, request.message, thread_id=request.thread_id, session_scope=session_scope)
        except ProviderUnavailable as exc:
            reservation.rollback()
            return JSONResponse({"reply": "AI provider is temporarily unavailable.", "stage": "collecting", "profile": TravelProfile().model_dump(mode="json"), "warnings": [exc.code]})
        except Exception:
            reservation.rollback()
            raise
        if result.error_code == "AGENT_UNAVAILABLE":
            reservation.rollback()
            return JSONResponse({"reply": result.reply, "stage": result.stage, "profile": TravelProfile.model_validate(result.profile).model_dump(mode="json"), "warnings": ["AI_PROVIDER_UNAVAILABLE"]})
        # Missing provider metadata is charged conservatively: one token per
        # model call, never a zero-cost successful request.
        reservation.commit(max(model_usage.input_tokens, model_usage.calls), model_usage.output_tokens)
        return ChatResponse(
            reply=result.reply,
            stage=result.stage,
            profile=TravelProfile.model_validate(result.profile),
        )
    except AppError as exc:
        raise HTTPException(status_code=ERROR_STATUS.get(exc.code, 503), detail=safe_error_detail(exc)) from None
    except ProviderUnavailable as exc:
        code = exc.code if exc.code in {"AI_RATE_LIMITED", "AI_UNAVAILABLE", "AI_CIRCUIT_OPEN"} else "AI_UNAVAILABLE"
        return JSONResponse({"reply": "AI provider is temporarily unavailable.", "stage": "collecting", "profile": TravelProfile().model_dump(mode="json"), "warnings": [code]})
    except Exception as exc:
        logging.getLogger("app.api.chat").warning(
            "chat_request_failed",
            extra={"error_code": "CHAT_UNAVAILABLE", "exception_type": type(exc).__name__},
        )
        raise HTTPException(
            status_code=503,
            detail={"code": "CHAT_UNAVAILABLE", "message": "Chat service is temporarily unavailable"},
        ) from None
