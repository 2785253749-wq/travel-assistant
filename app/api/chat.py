"""HTTP boundary for the legacy public chat contract."""

import logging

from fastapi import APIRouter, HTTPException

from app.agent.graph import chat
from app.schemas import ChatRequest, ChatResponse, TravelProfile


router = APIRouter()


@router.post("/api/chat", response_model=ChatResponse)
def api_chat(request: ChatRequest) -> ChatResponse:
    try:
        result = chat(None, None, request.message, thread_id=request.thread_id)
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
