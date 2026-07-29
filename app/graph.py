"""Compatibility facade for the pre-Agent graph imports."""

from typing import Any

from app.agent.graph import (
    FIELD_LABELS as LABELS,
    REQUIRED_FIELDS as REQUIRED,
    ChatResult,
    SafeTravelAgent,
    TravelState,
    extract_profile,
    model,
)
from app.schemas import TravelProfile


def extract(state: TravelState) -> dict[str, Any]:
    """Legacy extraction hook retained while callers migrate to ``app.agent.graph``."""
    current = TravelProfile.model_validate(state.get("profile") or {})
    profile = extract_profile(state["user_message"], current, model_factory=model)
    missing = [field for field in REQUIRED if getattr(profile, field) in (None, "")]
    return {"profile": profile.model_dump(), "missing_fields": missing}


def chat(message: str, thread_id: str) -> dict[str, Any]:
    result: ChatResult = SafeTravelAgent().run(message, trip=None)
    return {"reply": result.reply, "stage": result.stage, "profile": result.profile}
