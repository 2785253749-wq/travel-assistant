"""HTTP boundary for the legacy public chat contract."""

import logging

from fastapi import APIRouter, HTTPException

from app.agent.graph import chat
from app.schemas import ChatRequest, ChatResponse, TravelProfile


router = APIRouter()


@router.post("/api/chat", response_model=ChatResponse)
def api_chat(request: ChatRequest) -> ChatResponse:
    try:
        result = chat(None, None, request.message)
        return ChatResponse(
            reply=result.reply,
            stage=result.stage,
            profile=TravelProfile.model_validate(result.profile),
        )
    except Exception:
        logging.getLogger("app.api.chat").exception("chat_request_failed", extra={"error_type": "chat_failure"})
        raise HTTPException(
            status_code=503,
            detail={"code": "CHAT_UNAVAILABLE", "message": "Chat service is temporarily unavailable"},
        ) from None
