from app.core.config import Settings
from app.rag.models import RetrievedChunk
from app.rag.service import KnowledgeAnswerService, UnavailableKnowledgeAnswerService
from app.rag_v2.knowledge import RagV2KnowledgeAdapter, V2KnowledgeResult
from typing import get_type_hints


class FakeEmbedder:
    def embed(self, texts):
        return [[0.0] * 1024 for _ in texts]


class FakeRepository:
    def __init__(self) -> None:
        self.calls = []

    def search(self, vector, region, limit):
        self.calls.append((vector, region, limit))
        return [
            RetrievedChunk(
                chunk_id="grounded",
                content="轮渡是前往鼓浪屿的公共交通方式。",
                source_label="厦门市文化和旅游局",
                score=0.8,
            )
        ]


def test_missing_jina_key_builds_unavailable_service_without_constructing_http_path(
    monkeypatch,
) -> None:
    from app import composition

    def forbidden(*_args, **_kwargs):
        raise AssertionError("missing-key composition must not construct network dependencies")

    monkeypatch.setattr(composition, "JinaEmbedder", forbidden)
    monkeypatch.setattr(composition, "KnowledgeRepository", forbidden)

    service = composition.build_knowledge_answer_service(
        settings=Settings(jina_api_key=None, _env_file=None)
    )

    assert isinstance(service, UnavailableKnowledgeAnswerService)
    assert service.answer("厦门交通", "厦门").reply == "资料库没有足够依据，无法可靠回答。"


def test_composition_injects_repository_embedder_and_configured_threshold() -> None:
    from app.composition import build_knowledge_answer_service

    repository = FakeRepository()
    service = build_knowledge_answer_service(
        settings=Settings(
            jina_api_key="test-key",
            rag_similarity_threshold=0.81,
            _env_file=None,
        ),
        repository=repository,
        embedder=FakeEmbedder(),
    )

    assert isinstance(service, KnowledgeAnswerService)
    assert service.answer("厦门交通", "厦门").status == "refused"
    assert len(repository.calls) == 1
    assert repository.calls[0][1:] == ("厦门", 4)


def test_missing_private_repository_configuration_degrades_without_http_call() -> None:
    from app import composition

    called = False

    class RecordingClient:
        def post(self, *_args, **_kwargs):
            nonlocal called
            called = True
            raise AssertionError("incomplete composition must not call Jina")

    service = composition.build_knowledge_answer_service(
        settings=Settings(jina_api_key="test-key", _env_file=None),
        http_client=RecordingClient(),
    )

    assert isinstance(service, UnavailableKnowledgeAnswerService)
    assert service.answer("福建交通", "福建").status == "refused"
    assert called is False


def test_composition_requires_a_combined_search_and_quota_repository_protocol() -> None:
    from app.composition import KnowledgeRepositoryGateway, build_knowledge_answer_service

    assert get_type_hints(build_knowledge_answer_service)["repository"] == (
        KnowledgeRepositoryGateway | None
    )


def test_build_rag_v2_service_uses_injected_retrieval_without_network_construction(
    monkeypatch,
) -> None:
    from app import composition

    def forbidden(*_args, **_kwargs):
        raise AssertionError("injected retrieval must not construct live dependencies")

    monkeypatch.setattr(composition, "get_settings", forbidden)
    for name in (
        "RagV2Repository",
        "JinaQueryProvider",
        "JinaQueryEmbedder",
        "RetrievalService",
    ):
        monkeypatch.setattr(composition, name, forbidden, raising=False)

    class RetrievalThatMustNotRun:
        def retrieve(self, **_kwargs):
            raise AssertionError("composition must not retrieve during construction")

    retrieval = RetrievalThatMustNotRun()

    service = composition.build_rag_v2_knowledge_service(retrieval=retrieval)

    assert isinstance(service, RagV2KnowledgeAdapter)
    assert service._retrieval is retrieval


def test_missing_rag_v2_configuration_returns_unavailable_without_http_or_supabase(
    monkeypatch,
) -> None:
    from app import composition

    def forbidden(*_args, **_kwargs):
        raise AssertionError("missing configuration must not construct live dependencies")

    for name in (
        "RagV2Repository",
        "JinaQueryProvider",
        "JinaQueryEmbedder",
        "RetrievalService",
    ):
        monkeypatch.setattr(composition, name, forbidden, raising=False)

    settings = Settings(
        jina_api_key=None,
        supabase_url=None,
        supabase_service_key=None,
        _env_file=None,
    )

    service = composition.build_rag_v2_knowledge_service(settings=settings)
    from app.rag_v2.knowledge import UnavailableRagV2KnowledgeAdapter

    assert isinstance(service, UnavailableRagV2KnowledgeAdapter)
    assert service.answer("厦门鼓浪屿有什么特点？") == V2KnowledgeResult(
        status="unavailable",
        reply=None,
        evidence=(),
        error_code="RAG_V2_UNAVAILABLE",
    )


def test_build_rag_v2_service_constructs_configured_dependency_chain(monkeypatch) -> None:
    from app import composition

    settings = Settings(
        jina_api_key="test-key",
        supabase_url="https://supabase.example.test",
        supabase_service_key="service-key",
        _env_file=None,
    )
    http_client = object()
    calls = {"retrieve": 0, "embed_query": 0}

    class RecordingRepository:
        def __init__(self, *, settings):
            self.settings = settings

    class RecordingProvider:
        def __init__(self, *, api_key, timeout_seconds, client):
            self.api_key = api_key
            self.timeout_seconds = timeout_seconds
            self.client = client

    class RecordingQueryEmbedder:
        def __init__(self, *, provider):
            self.provider = provider

        def embed_query(self, _query):
            calls["embed_query"] += 1
            raise AssertionError("construction must not embed a query")

    class RecordingRetrievalService:
        def __init__(self, *, embedder, repository):
            self.embedder = embedder
            self.repository = repository

        def retrieve(self, **_kwargs):
            calls["retrieve"] += 1
            raise AssertionError("construction must not retrieve")

    monkeypatch.setattr(composition, "RagV2Repository", RecordingRepository)
    monkeypatch.setattr(composition, "JinaQueryProvider", RecordingProvider)
    monkeypatch.setattr(composition, "JinaQueryEmbedder", RecordingQueryEmbedder)
    monkeypatch.setattr(composition, "RetrievalService", RecordingRetrievalService)

    service = composition.build_rag_v2_knowledge_service(
        settings=settings,
        http_client=http_client,
    )

    assert isinstance(service, RagV2KnowledgeAdapter)
    retrieval = service._retrieval
    assert isinstance(retrieval, RecordingRetrievalService)
    assert isinstance(retrieval.repository, RecordingRepository)
    assert retrieval.repository.settings is settings
    assert isinstance(retrieval.embedder, RecordingQueryEmbedder)
    provider = retrieval.embedder.provider
    assert isinstance(provider, RecordingProvider)
    assert provider.api_key is settings.jina_api_key
    assert provider.timeout_seconds == settings.weather_timeout_seconds
    assert provider.client is http_client
    assert calls == {"retrieve": 0, "embed_query": 0}


def test_get_rag_v2_knowledge_service_uses_cached_builder_once(monkeypatch) -> None:
    from app import composition

    sentinel = object()
    builder_calls = []

    def recording_builder():
        builder_calls.append(object())
        return sentinel

    monkeypatch.setattr(
        composition,
        "build_rag_v2_knowledge_service",
        recording_builder,
    )
    getter = getattr(composition, "get_rag_v2_knowledge_service", None)
    if getter is not None:
        getter.cache_clear()
    try:
        first = composition.get_rag_v2_knowledge_service()
        second = composition.get_rag_v2_knowledge_service()
        assert len(builder_calls) == 1
        assert first is sentinel
        assert second is first
    finally:
        getter = getattr(composition, "get_rag_v2_knowledge_service", None)
        if getter is not None:
            getter.cache_clear()
