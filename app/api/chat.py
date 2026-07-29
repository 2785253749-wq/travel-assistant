"""HTTP boundary for the legacy public chat contract."""

import hashlib
import logging
import os
import re
import secrets
from typing import Annotated

from fastapi import APIRouter, Cookie, HTTPException, Response

from app.agent.graph import chat
from app.api.auth import OptionalCurrentUser
from app.schemas import ChatRequest, ChatResponse, TravelProfile


router = APIRouter()
_SESSION_COOKIE = "travel_session"
_OPAQUE_SESSION = re.compile(r"^[A-Za-z0-9_-]{32,128}$")


@router.post("/api/chat", response_model=ChatResponse)
def api_chat(
    request: ChatRequest,
    response: Response,
    user: OptionalCurrentUser,
    anonymous_session: Annotated[str | None, Cookie(alias=_SESSION_COOKIE)] = None,
) -> ChatResponse:
    try:
        if user is not None:
            session_scope = f"user:{user.id}"
        else:
            opaque_session = (
                anonymous_session
                if anonymous_session and _OPAQUE_SESSION.fullmatch(anonymous_session)
                else secrets.token_urlsafe(32)
            )
            session_scope = "anon:" + hashlib.sha256(opaque_session.encode("ascii")).hexdigest()
            if opaque_session != anonymous_session:
                response.set_cookie(
                    _SESSION_COOKIE,
                    opaque_session,
                    httponly=True,
                    samesite="lax",
                    secure=os.environ.get("APP_ENV", "development").lower() == "production",
                    max_age=60 * 60 * 24,
                )
        result = chat(
            user, None, request.message,
            thread_id=request.thread_id,
            session_scope=session_scope,
        )
        return ChatResponse(
            reply=result.reply,
            stage=result.stage,
            profile=TravelProfile.model_validate(result.profile),
        )
    except Exception as exc:
        logging.getLogger("app.api.chat").warning(
            "chat_request_failed",
            extra={"error_code": "CHAT_UNAVAILABLE", "exception_type": type(exc).__name__},
        )
        raise HTTPException(
            status_code=503,
            detail={"code": "CHAT_UNAVAILABLE", "message": "Chat service is temporarily unavailable"},
        ) from None
