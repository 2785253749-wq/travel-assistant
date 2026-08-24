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
from app.application.weather import UnavailableWeatherService, WeatherService
from app.api.auth import CurrentUser, OptionalCurrentUser
from app.core.config import Settings, get_settings
from app.core.usage import InMemoryUsageRepository, UsageGuard, UsageRepository
from app.infrastructure.repositories import (
    InMemoryTripRepository,
    create_public_share_repository,
    create_user_scoped_supabase_repository,
)
from app.infrastructure.usage import SupabaseUsageRepository
from app.infrastructure.weather import SupabaseWeatherQuotaRepository
from app.providers.aggregate import ProviderEvidenceAggregator
from app.providers.amap_weather import AmapWeatherProvider
from app.rag.embedding import EmbeddingHttpClient, EmbeddingQuota, JinaEmbedder
from app.rag.repository import KnowledgeRepository
from app.rag.service import (
    Embedder,
    KnowledgeAnswerService,
    SearchRepository,
    UnavailableKnowledgeAnswerService,
)
from app.schemas import TravelProfile
from app.travel_notes.in_memory import (
    InMemoryTravelNoteMediaGateway,
    InMemoryTravelNoteRepository,
)
from app.travel_notes.service import TravelNoteModule
from app.travel_notes.supabase_repositories import (
    create_internal_supabase_client,
    create_public_travel_note_repository,
    create_travel_note_media_gateway,
    create_user_scoped_travel_note_repository,
)
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


def _supabase_internal_credentials() -> tuple[str, str]:
    settings = get_settings()
    if settings.supabase_url is None or settings.supabase_service_key is None:
        raise RuntimeError("Supabase internal storage is not configured")
    return (
        str(settings.supabase_url),
        settings.supabase_service_key.get_secret_value(),
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


@lru_cache(maxsize=1)
def get_development_travel_note_repository() -> InMemoryTravelNoteRepository:
    return InMemoryTravelNoteRepository()


@lru_cache(maxsize=1)
def get_development_travel_note_module() -> TravelNoteModule:
    repository = get_development_travel_note_repository()
    return TravelNoteModule(
        repository=repository,
        public_repository=repository,
        media_gateway=InMemoryTravelNoteMediaGateway(),
    )


@lru_cache(maxsize=1)
def get_travel_note_internal_client():
    if not _uses_supabase():
        raise RuntimeError("Travel note internal storage is not configured")
    url, service_key = _supabase_internal_credentials()
    return create_internal_supabase_client(url, service_key)


@lru_cache(maxsize=1)
def get_public_travel_note_repository():
    if not _uses_supabase():
        return get_development_travel_note_repository()
    url, service_key = _supabase_internal_credentials()
    return create_public_travel_note_repository(
        url, service_key, client=get_travel_note_internal_client()
    )


@lru_cache(maxsize=1)
def get_travel_note_media_gateway():
    if not _uses_supabase():
        return InMemoryTravelNoteMediaGateway()
    url, service_key = _supabase_internal_credentials()
    return create_travel_note_media_gateway(
        url, service_key, client=get_travel_note_internal_client()
    )


class _AnonymousTravelNoteRepository:
    def create_draft(self, *args, **kwargs):
        raise RuntimeError("Anonymous travel note creation is unavailable")

    def replace_draft(self, *args, **kwargs):
        raise RuntimeError("Anonymous travel note editing is unavailable")

    def attach_image(self, *args, **kwargs):
        raise RuntimeError("Anonymous travel note media changes are unavailable")

    def remove_image(self, *args, **kwargs):
        raise RuntimeError("Anonymous travel note media changes are unavailable")

    def get_owned(self, user_id: UUID, note_id: UUID):
        del user_id, note_id
        return None

    def get_note(self, note_id: UUID):
        del note_id
        return None

    def submit(self, *args, **kwargs):
        raise RuntimeError("Anonymous travel note submission is unavailable")

    def soft_delete(self, user_id: UUID, note_id: UUID, *, now):
        del user_id, note_id, now
        return False

    def list_owned(self, user_id: UUID):
        del user_id
        return []

    def get_source_trip_snapshot(self, user_id: UUID, trip_id: UUID):
        del user_id, trip_id
        return None

    def approve(self, *args, **kwargs):
        raise RuntimeError("Anonymous travel note moderation is unavailable")

    def reject(self, *args, **kwargs):
        raise RuntimeError("Anonymous travel note moderation is unavailable")


def get_travel_note_module(user: CurrentUser) -> TravelNoteModule:
    if not _uses_supabase():
        return get_development_travel_note_module()
    if not user.access_token:
        raise RuntimeError("A verified bearer token is required for travel note access")
    url, anon_key = _supabase_public_credentials()
    return TravelNoteModule(
        repository=create_user_scoped_travel_note_repository(
            url,
            anon_key,
            user.access_token,
            internal_client=get_travel_note_internal_client(),
        ),
        public_repository=get_public_travel_note_repository(),
        media_gateway=get_travel_note_media_gateway(),
    )


def get_optional_travel_note_module(user: OptionalCurrentUser) -> TravelNoteModule:
    if not _uses_supabase():
        return get_development_travel_note_module()
    if user is not None and user.access_token:
        url, anon_key = _supabase_public_credentials()
        private_repository = create_user_scoped_travel_note_repository(
            url,
            anon_key,
            user.access_token,
            internal_client=get_travel_note_internal_client(),
        )
    else:
        private_repository = _AnonymousTravelNoteRepository()
    return TravelNoteModule(
        repository=private_repository,
        public_repository=get_public_travel_note_repository(),
        media_gateway=get_travel_note_media_gateway(),
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


def build_weather_service(
    *, settings: Settings | None = None
) -> WeatherService | UnavailableWeatherService:
    settings = settings or get_settings()
    if (
        settings.amap_web_service_key is None
        or not settings.amap_web_service_key.get_secret_value().strip()
    ):
        return UnavailableWeatherService()
    quota = None
    if settings.app_env == "production":
        if settings.supabase_url is None or settings.supabase_service_key is None:
            raise RuntimeError("server-side weather quota storage is not configured")
        from supabase import create_client

        quota = SupabaseWeatherQuotaRepository(
            create_client(
                str(settings.supabase_url),
                settings.supabase_service_key.get_secret_value()
            )
        )
    return WeatherService(
        provider=AmapWeatherProvider(settings=settings),
        cache_ttl_seconds=settings.weather_cache_seconds,
        daily_limit=settings.weather_daily_limit,
        quota=quota,
    )


@lru_cache(maxsize=1)
def get_weather_service() -> WeatherService | UnavailableWeatherService:
    return build_weather_service()


def build_chat_application(user: Any | None) -> TravelChatApplication:
    """The sole concrete composition root for the public chat use case."""
    providers = get_provider_evidence_aggregator()

    def agent_factory(initial_profile: TravelProfile) -> SafeTravelAgent:
        return SafeTravelAgent(
            classifier=RuleIntentClassifier(),
            extractor=RuleTravelExtractor(),
            planner=ModelStructuredPlanner(),
            evidence_provider=providers,
            knowledge=get_knowledge_answer_service(),
            weather=get_weather_service(),
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

