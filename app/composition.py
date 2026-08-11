from __future__ import annotations

from functools import lru_cache
from typing import Any, Protocol
from uuid import UUID

from app.agent.graph import (
    ModelStructuredPlanner,
    RuleIntentClassifier,
    RuleTravelExtractor,
    SafeTravelAgent,
)
from app.application.chat import ConfirmationStore, TravelChatApplication
from app.api.auth import CurrentUser
from app.core.config import Settings, get_settings
from app.core.usage import InMemoryUsageRepository, UsageGuard, UsageRepository
from app.infrastructure.repositories import (
    InMemoryTripRepository,
    create_public_share_repository,
    create_user_scoped_supabase_repository,
)
from app.infrastructure.usage import SupabaseUsageRepository
from app.providers.aggregate import ProviderEvidenceAggregator
from app.rag.embedding import EmbeddingHttpClient, EmbeddingQuota, JinaEmbedder
from app.rag.repository import KnowledgeRepository
from app.rag.service import (
    Embedder,
    KnowledgeAnswerService,
    SearchRepository,
    UnavailableKnowledgeAnswerService,
)
from app.schemas import TravelProfile
from app.trips.service import TripService


_confirmation_store = ConfirmationStore()
_usage_repository = InMemoryUsageRepository()


class KnowledgeRepositoryGateway(SearchRepository, EmbeddingQuota, Protocol):
    """Private runtime dependency providing both retrieval and atomic quota reserve."""


def _uses_supabase() -> bool:
    settings = get_settings()
    return settings.app_env == "production" or (
        settings.supabase_url is not None and settings.supabase_anon_key is not None
    )


@lru_cache(maxsize=1)
def get_development_repository() -> InMemoryTripRepository:
    """Share one credential-free store across local private and public dependencies."""
    return InMemoryTripRepository()


def _supabase_public_credentials() -> tuple[str, str]:
    settings = get_settings()
    if settings.supabase_url is None or settings.supabase_anon_key is None:
        raise RuntimeError("Supabase trip storage is not configured")
    return (
        str(settings.supabase_url),
        settings.supabase_anon_key.get_secret_value(),
    )


def get_trip_service(user: CurrentUser) -> TripService:
    if not _uses_supabase():
        return TripService(get_development_repository())
    if not user.access_token:
        raise RuntimeError("A verified bearer token is required for Supabase trip access")
    url, anon_key = _supabase_public_credentials()
    return TripService(
        create_user_scoped_supabase_repository(url, anon_key, user.access_token)
    )


def get_public_trip_service() -> TripService:
    if not _uses_supabase():
        return TripService(get_development_repository())
    url, anon_key = _supabase_public_credentials()
    return TripService(
        InMemoryTripRepository(), create_public_share_repository(url, anon_key)
    )


def get_usage_guard() -> UsageGuard:
    settings = get_settings()
    configured = settings.deepseek_api_key is not None and bool(
        settings.deepseek_api_key.get_secret_value().strip()
    )
    repository: UsageRepository = _usage_repository
    if settings.app_env == "production":
        if settings.supabase_url is None or settings.supabase_service_key is None:
            raise RuntimeError("server-side usage storage is not configured")
        from supabase import create_client

        repository = SupabaseUsageRepository(
            create_client(
                str(settings.supabase_url),
                settings.supabase_service_key.get_secret_value(),
            )
        )
    return UsageGuard(
        repository=repository,
        user_daily_limit=settings.ai_user_daily_limit,
        global_daily_limit=settings.ai_global_daily_limit,
        enabled=settings.ai_enabled,
        provider_configured=configured,
        input_cost_micros_per_million_tokens=(
            settings.ai_input_cost_micros_per_million_tokens
        ),
        output_cost_micros_per_million_tokens=(
            settings.ai_output_cost_micros_per_million_tokens
        ),
    )


@lru_cache(maxsize=1)
def get_provider_evidence_aggregator() -> ProviderEvidenceAggregator:
    """Keep the short provider cache alive across request-scoped applications."""
    return ProviderEvidenceAggregator()


def build_knowledge_answer_service(
    *,
    settings: Settings | None = None,
    repository: KnowledgeRepositoryGateway | None = None,
    embedder: Embedder | None = None,
    http_client: EmbeddingHttpClient | None = None,
) -> KnowledgeAnswerService | UnavailableKnowledgeAnswerService:
    """Compose private retrieval only when its server-side dependencies exist."""
    settings = settings or get_settings()
    if settings.jina_api_key is None or not settings.jina_api_key.get_secret_value().strip():
        return UnavailableKnowledgeAnswerService()
    if repository is None:
        if (
            settings.supabase_url is None
            or settings.supabase_service_key is None
            or not settings.supabase_service_key.get_secret_value().strip()
        ):
            return UnavailableKnowledgeAnswerService()
        repository = KnowledgeRepository(settings=settings)
    if embedder is None:
        embedder = JinaEmbedder(
            api_key=settings.jina_api_key,
            model=settings.rag_embedding_model,
            timeout_seconds=settings.weather_timeout_seconds,
            daily_limit=settings.rag_daily_embedding_limit,
            quota=repository,
            client=http_client,
        )
    return KnowledgeAnswerService(
        repository,
        embedder,
        threshold=settings.rag_similarity_threshold,
    )


@lru_cache(maxsize=1)
def get_knowledge_answer_service(
) -> KnowledgeAnswerService | UnavailableKnowledgeAnswerService:
    return build_knowledge_answer_service()


def build_chat_application(user: Any | None) -> TravelChatApplication:
    """The sole concrete composition root for the public chat use case."""
    providers = get_provider_evidence_aggregator()

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
