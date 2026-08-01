from __future__ import annotations

from typing import Any
from uuid import UUID

from app.agent.graph import (
    ModelStructuredPlanner,
    RuleIntentClassifier,
    RuleTravelExtractor,
    SafeTravelAgent,
)
from app.application.chat import ConfirmationStore, TravelChatApplication
from app.core.usage import get_usage_guard
from app.providers.aggregate import ProviderEvidenceAggregator
from app.schemas import TravelProfile
from app.trips.service import get_trip_service


_confirmation_store = ConfirmationStore()


def build_chat_application(user: Any | None) -> TravelChatApplication:
    """The sole concrete composition root for the public chat use case."""
    providers = ProviderEvidenceAggregator()

    def agent_factory(initial_profile: TravelProfile) -> SafeTravelAgent:
        return SafeTravelAgent(
            classifier=RuleIntentClassifier(),
            extractor=RuleTravelExtractor(),
            planner=ModelStructuredPlanner(),
            evidence_provider=providers,
            initial_profile=initial_profile,
        )

    return TravelChatApplication(
        agent_factory=agent_factory,
        usage_guard=get_usage_guard(),
        confirmation_store=_confirmation_store,
        trip_service=get_trip_service(user) if user is not None else None,
    )


def execute_chat_request(
    user: Any | None,
    trip_id: UUID | None,
    message: str,
    *,
    thread_id: str,
    session_scope: str,
    quota_subject: str,
    action: str,
):
    application = build_chat_application(user)
    arguments = {
        "user_id": getattr(user, "id", None),
        "subject": session_scope,
        "thread_id": thread_id,
        "trip_id": trip_id,
        "message": message,
    }
    if action == "collect":
        return application.collect(**arguments)
    if action == "confirm":
        return application.confirm(**arguments, quota_subject=quota_subject)
    raise ValueError("unsupported chat action")
