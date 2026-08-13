from app.core.config import Settings
from app.rag.models import RetrievedChunk
from app.rag.service import KnowledgeAnswerService, UnavailableKnowledgeAnswerService
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
            jina_api_key="server-only-secret",
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
        settings=Settings(jina_api_key="server-only-secret", _env_file=None),
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
