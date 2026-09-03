from __future__ import annotations

from dataclasses import FrozenInstanceError, fields
from datetime import date
from inspect import signature
from math import inf, nan
from typing import Protocol, get_type_hints
from uuid import UUID

import pytest

from app import rag_v2
from app.core.errors import AppError
from app.rag_v2.models import ChunkType
from app.rag_v2.models import DestinationLevel
from app.rag_v2.repository import RagV2Candidate
from app.rag_v2 import retrieval as retrieval_module
from app.rag_v2.retrieval import (
    QueryEmbedder,
    RetrievalEvidence,
    RetrievalResult,
    RetrievalService,
)


_ATTRACTION_ID = UUID("11111111-1111-1111-1111-111111111111")
_ATTRACTION_A = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
_ATTRACTION_B = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
_ATTRACTION_C = UUID("cccccccc-cccc-cccc-cccc-cccccccccccc")
_QUERY_VECTOR = tuple([0.25] * 1024)


def _evidence(*, content: str = "厦门适合看日落") -> RetrievalEvidence:
    return RetrievalEvidence(
        attraction_id=_ATTRACTION_ID,
        chunk_key="overview-0",
        chunk_type=ChunkType.overview,
        content=content,
        content_hash="a" * 64,
        source_label="official-guide",
        source_url="https://example.test/xiamen",
        source_type="official",
        reviewed_on=date(2026, 9, 3),
        score=0.95,
    )


class FakeEmbedder:
    def __init__(
        self,
        *,
        vector: tuple[float, ...] = _QUERY_VECTOR,
        error: AppError | None = None,
    ) -> None:
        self.vector = vector
        self.error = error
        self.calls: list[str] = []

    def embed_query(self, query: str) -> tuple[float, ...]:
        self.calls.append(query)
        if self.error is not None:
            raise self.error
        return self.vector


class FakeRepository:
    def __init__(
        self,
        *,
        candidates: tuple[RagV2Candidate, ...] = (),
        error: AppError | None = None,
    ) -> None:
        self.candidates = candidates
        self.error = error
        self.calls: list[dict[str, object]] = []

    def match_chunks(self, **kwargs: object) -> tuple[RagV2Candidate, ...]:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.candidates


def _candidate(
    *,
    attraction_id: UUID,
    chunk_key: str,
    chunk_type: ChunkType = ChunkType.overview,
    score: float = 0.95,
    content_hash: str | None = None,
    content: str | None = None,
) -> RagV2Candidate:
    return RagV2Candidate(
        corpus_version_id=UUID("99999999-9999-9999-9999-999999999999"),
        attraction_id=attraction_id,
        chunk_key=chunk_key,
        chunk_type=chunk_type,
        content=content or f"content-{chunk_key}",
        content_hash=content_hash or f"hash-{chunk_key}",
        source_label="source-label",
        source_url=f"https://example.test/{chunk_key}",
        source_type="official",
        reviewed_on=date(2026, 9, 3),
        score=score,
    )


def _service(
    *,
    candidates: tuple[RagV2Candidate, ...] = (),
    embedder: FakeEmbedder | None = None,
    repository: FakeRepository | None = None,
) -> tuple[RetrievalService, FakeEmbedder, FakeRepository]:
    actual_embedder = embedder or FakeEmbedder()
    actual_repository = repository or FakeRepository(candidates=candidates)
    return (
        RetrievalService(embedder=actual_embedder, repository=actual_repository),
        actual_embedder,
        actual_repository,
    )


def test_query_embedder_is_a_structural_protocol_with_exact_method() -> None:
    assert issubclass(QueryEmbedder, Protocol)

    method_signature = signature(QueryEmbedder.embed_query)
    assert tuple(method_signature.parameters) == ("self", "query")
    assert method_signature.parameters["query"].kind.name == "POSITIONAL_OR_KEYWORD"
    assert get_type_hints(QueryEmbedder.embed_query) == {
        "query": str,
        "return": tuple[float, ...],
    }


def test_retrieval_evidence_has_exact_frozen_field_order() -> None:
    assert tuple(field.name for field in fields(RetrievalEvidence)) == (
        "attraction_id",
        "chunk_key",
        "chunk_type",
        "content",
        "content_hash",
        "source_label",
        "source_url",
        "source_type",
        "reviewed_on",
        "score",
    )

    evidence = _evidence()
    with pytest.raises(FrozenInstanceError):
        evidence.score = 0.5


def test_retrieval_evidence_excludes_storage_identity_and_extra_metadata() -> None:
    field_names = {field.name for field in fields(RetrievalEvidence)}

    assert field_names.isdisjoint(
        {
            "corpus_version_id",
            "embedding",
            "embedding_model",
            "embedding_task",
            "embedding_dimensions",
            "status",
            "rank",
            "debug",
            "summary",
            "citation",
        }
    )
    assert not hasattr(_evidence(), "corpus_version_id")


def test_retrieval_result_has_exact_frozen_field_order() -> None:
    assert tuple(field.name for field in fields(RetrievalResult)) == (
        "query",
        "evidence",
    )

    result = RetrievalResult(query="厦门日落", evidence=())
    with pytest.raises(FrozenInstanceError):
        result.query = "福州日落"


def test_retrieval_result_preserves_supplied_evidence_tuple_and_values() -> None:
    first = _evidence()
    second = _evidence(content="厦门交通")
    evidence = (first, second)

    result = RetrievalResult(query="厦门日落", evidence=evidence)

    assert result.query == "厦门日落"
    assert result.evidence is evidence
    assert result.evidence == (first, second)
    assert result.evidence[0] is first
    assert result.evidence[1] is second


def test_task2_module_contracts_remain_available_after_task3() -> None:
    assert retrieval_module.QueryEmbedder is QueryEmbedder
    assert retrieval_module.RetrievalEvidence is RetrievalEvidence
    assert retrieval_module.RetrievalResult is RetrievalResult
    assert not hasattr(retrieval_module, "retrieve")


def test_task2_symbols_remain_module_level_and_root_exports_stay_frozen() -> None:
    assert "QueryEmbedder" not in rag_v2.__all__
    assert "RetrievalEvidence" not in rag_v2.__all__
    assert "RetrievalResult" not in rag_v2.__all__
    assert len(rag_v2.__all__) == 44


def test_retrieval_service_constructor_and_method_have_exact_keyword_only_api() -> None:
    constructor_parameters = signature(RetrievalService.__init__).parameters
    assert tuple(constructor_parameters) == ("self", "embedder", "repository")
    assert constructor_parameters["embedder"].kind.name == "KEYWORD_ONLY"
    assert constructor_parameters["repository"].kind.name == "KEYWORD_ONLY"

    retrieve_parameters = signature(RetrievalService.retrieve).parameters
    assert tuple(retrieve_parameters) == (
        "self",
        "query",
        "dataset_key",
        "destination_code",
        "destination_level",
        "province_code",
        "attraction_id",
        "candidate_k",
        "final_k",
        "score_threshold",
    )
    assert all(
        parameter.kind.name == "KEYWORD_ONLY"
        for name, parameter in retrieve_parameters.items()
        if name != "self"
    )
    assert get_type_hints(RetrievalService.retrieve) == {
        "query": str,
        "dataset_key": str,
        "destination_code": str | None,
        "destination_level": DestinationLevel | None,
        "province_code": str | None,
        "attraction_id": UUID | None,
        "candidate_k": int,
        "final_k": int,
        "score_threshold": float,
        "return": RetrievalResult,
    }
    assert retrieve_parameters["candidate_k"].default == 40
    assert retrieve_parameters["final_k"].default == 6
    assert retrieve_parameters["score_threshold"].default == 0.70


def test_retrieve_strips_outer_query_whitespace_once_and_preserves_internal_spacing() -> None:
    service, embedder, _ = _service()

    result = service.retrieve(
        query="  厦门   日落  ",
        dataset_key="travel-attractions-cn",
    )

    assert embedder.calls == ["厦门   日落"]
    assert result.query == "厦门   日落"


@pytest.mark.parametrize("query", [None, 123, object(), " \t\n "])
def test_retrieve_rejects_invalid_query_before_downstream_calls(query: object) -> None:
    service, embedder, repository = _service()

    with pytest.raises(ValueError) as raised:
        service.retrieve(query=query, dataset_key="travel-attractions-cn")

    assert str(raised.value) == "query must be a non-empty string"
    assert embedder.calls == []
    assert repository.calls == []


def test_retrieve_validates_query_before_later_invalid_parameters() -> None:
    service, embedder, repository = _service()

    with pytest.raises(ValueError) as raised:
        service.retrieve(
            query=None,
            dataset_key="travel-attractions-cn",
            final_k=0,
            score_threshold=nan,
        )

    assert str(raised.value) == "query must be a non-empty string"
    assert embedder.calls == []
    assert repository.calls == []


def test_retrieve_validates_final_k_before_score_threshold() -> None:
    service, embedder, repository = _service()

    with pytest.raises(ValueError) as raised:
        service.retrieve(
            query="厦门日落",
            dataset_key="travel-attractions-cn",
            final_k=0,
            score_threshold=nan,
        )

    assert str(raised.value) == "final_k must be at least 1"
    assert embedder.calls == []
    assert repository.calls == []


@pytest.mark.parametrize("final_k", [True, False, 1.5, "6", None, 0, -1])
def test_retrieve_rejects_invalid_final_k_before_downstream_calls(final_k: object) -> None:
    service, embedder, repository = _service()

    with pytest.raises(ValueError) as raised:
        service.retrieve(
            query="厦门日落",
            dataset_key="travel-attractions-cn",
            final_k=final_k,
        )

    assert str(raised.value) == "final_k must be at least 1"
    assert embedder.calls == []
    assert repository.calls == []


@pytest.mark.parametrize(
    "score_threshold",
    [True, False, "0.70", None, nan, inf, -inf],
)
def test_retrieve_rejects_invalid_score_threshold_before_downstream_calls(
    score_threshold: object,
) -> None:
    service, embedder, repository = _service()

    with pytest.raises(ValueError) as raised:
        service.retrieve(
            query="厦门日落",
            dataset_key="travel-attractions-cn",
            score_threshold=score_threshold,
        )

    assert str(raised.value) == "score_threshold must be finite"
    assert embedder.calls == []
    assert repository.calls == []


@pytest.mark.parametrize("score_threshold", [0, 1, 0.70, -0.25])
def test_retrieve_accepts_finite_numeric_score_thresholds(
    score_threshold: int | float,
) -> None:
    service, embedder, repository = _service()

    result = service.retrieve(
        query="厦门日落",
        dataset_key="travel-attractions-cn",
        score_threshold=score_threshold,
    )

    assert result.evidence == ()
    assert embedder.calls == ["厦门日落"]
    assert len(repository.calls) == 1


def test_retrieve_forwards_normalized_vector_and_all_repository_filters_exactly() -> None:
    service, embedder, repository = _service()

    service.retrieve(
        query="  厦门 日落  ",
        dataset_key="travel-attractions-cn",
        destination_code="350200",
        destination_level=DestinationLevel.prefecture_city,
        province_code="350000",
        attraction_id=_ATTRACTION_A,
        candidate_k=17,
    )

    assert embedder.calls == ["厦门 日落"]
    assert repository.calls == [
        {
            "dataset_key": "travel-attractions-cn",
            "query_embedding": _QUERY_VECTOR,
            "destination_code": "350200",
            "destination_level": DestinationLevel.prefecture_city,
            "province_code": "350000",
            "attraction_id": _ATTRACTION_A,
            "candidate_k": 17,
        }
    ]
    assert repository.calls[0]["query_embedding"] is _QUERY_VECTOR


def test_candidate_k_is_forwarded_unchanged_without_python_range_validation() -> None:
    service, _, repository = _service()

    service.retrieve(
        query="厦门日落",
        dataset_key="travel-attractions-cn",
        candidate_k=0,
    )

    assert repository.calls[0]["candidate_k"] == 0


def test_retrieve_preserves_repository_order_without_python_sorting() -> None:
    candidates = (
        _candidate(attraction_id=_ATTRACTION_A, chunk_key="first", score=0.80),
        _candidate(attraction_id=_ATTRACTION_B, chunk_key="second", score=0.95),
    )
    service, _, _ = _service(candidates=candidates)

    result = service.retrieve(
        query="厦门日落",
        dataset_key="travel-attractions-cn",
        score_threshold=0,
        final_k=2,
    )

    assert tuple(item.chunk_key for item in result.evidence) == ("first", "second")


def test_retrieve_retains_threshold_equality_and_rejects_lower_scores() -> None:
    candidates = (
        _candidate(attraction_id=_ATTRACTION_A, chunk_key="equal", score=0.70),
        _candidate(attraction_id=_ATTRACTION_B, chunk_key="lower", score=0.6999),
    )
    service, _, _ = _service(candidates=candidates)

    result = service.retrieve(
        query="厦门日落",
        dataset_key="travel-attractions-cn",
        score_threshold=0.70,
    )

    assert tuple(item.chunk_key for item in result.evidence) == ("equal",)


def test_retrieve_deduplicates_by_first_content_hash_occurrence() -> None:
    candidates = (
        _candidate(
            attraction_id=_ATTRACTION_A,
            chunk_key="earlier",
            score=0.71,
            content_hash="same",
        ),
        _candidate(
            attraction_id=_ATTRACTION_B,
            chunk_key="later-higher",
            score=0.99,
            content_hash="same",
        ),
    )
    service, _, _ = _service(candidates=candidates)

    result = service.retrieve(
        query="厦门日落",
        dataset_key="travel-attractions-cn",
        score_threshold=0.70,
        final_k=2,
    )

    assert tuple(item.chunk_key for item in result.evidence) == ("earlier",)


def test_retrieve_applies_two_round_attraction_diversity_in_stable_order() -> None:
    candidates = (
        _candidate(
            attraction_id=_ATTRACTION_A,
            chunk_key="a-overview",
            chunk_type=ChunkType.overview,
            score=0.95,
        ),
        _candidate(
            attraction_id=_ATTRACTION_A,
            chunk_key="a-transport",
            chunk_type=ChunkType.transport,
            score=0.93,
        ),
        _candidate(
            attraction_id=_ATTRACTION_B,
            chunk_key="b-overview",
            chunk_type=ChunkType.overview,
            score=0.90,
        ),
        _candidate(
            attraction_id=_ATTRACTION_C,
            chunk_key="c-overview",
            chunk_type=ChunkType.overview,
            score=0.88,
        ),
        _candidate(
            attraction_id=_ATTRACTION_A,
            chunk_key="a-seasonal",
            chunk_type=ChunkType.seasonal,
            score=0.86,
        ),
    )
    service, _, _ = _service(candidates=candidates)

    result = service.retrieve(
        query="厦门日落",
        dataset_key="travel-attractions-cn",
        final_k=4,
        score_threshold=0,
    )

    assert tuple(item.chunk_key for item in result.evidence) == (
        "a-overview",
        "b-overview",
        "c-overview",
        "a-transport",
    )


@pytest.mark.parametrize(
    ("final_k", "expected_keys"),
    [
        (1, ("a-overview",)),
        (6, ("a-overview", "b-overview", "c-overview", "a-transport", "a-seasonal")),
    ],
)
def test_retrieve_applies_final_k_and_returns_available_short_results(
    final_k: int,
    expected_keys: tuple[str, ...],
) -> None:
    candidates = (
        _candidate(attraction_id=_ATTRACTION_A, chunk_key="a-overview"),
        _candidate(
            attraction_id=_ATTRACTION_A,
            chunk_key="a-transport",
            chunk_type=ChunkType.transport,
            score=0.93,
        ),
        _candidate(attraction_id=_ATTRACTION_B, chunk_key="b-overview", score=0.90),
        _candidate(attraction_id=_ATTRACTION_C, chunk_key="c-overview", score=0.88),
        _candidate(
            attraction_id=_ATTRACTION_A,
            chunk_key="a-seasonal",
            chunk_type=ChunkType.seasonal,
            score=0.86,
        ),
    )
    service, _, _ = _service(candidates=candidates)

    result = service.retrieve(
        query="厦门日落",
        dataset_key="travel-attractions-cn",
        final_k=final_k,
        score_threshold=0,
    )

    assert tuple(item.chunk_key for item in result.evidence) == expected_keys


def test_retrieve_maps_all_candidate_fields_without_storage_identity() -> None:
    candidate = RagV2Candidate(
        corpus_version_id=UUID("99999999-9999-9999-9999-999999999999"),
        attraction_id=_ATTRACTION_A,
        chunk_key="mapped",
        chunk_type=ChunkType.highlights,
        content="原始内容",
        content_hash="content-hash",
        source_label="source-label",
        source_url="https://example.test/source",
        source_type="official",
        reviewed_on=date(2026, 9, 2),
        score=0.83,
    )
    service, _, _ = _service(candidates=(candidate,))

    result = service.retrieve(
        query="厦门日落",
        dataset_key="travel-attractions-cn",
        score_threshold=0,
    )

    assert result.evidence == (
        RetrievalEvidence(
            attraction_id=_ATTRACTION_A,
            chunk_key="mapped",
            chunk_type=ChunkType.highlights,
            content="原始内容",
            content_hash="content-hash",
            source_label="source-label",
            source_url="https://example.test/source",
            source_type="official",
            reviewed_on=date(2026, 9, 2),
            score=0.83,
        ),
    )
    assert not hasattr(result.evidence[0], "corpus_version_id")


@pytest.mark.parametrize(
    "candidates",
    [
        (),
        (
            _candidate(
                attraction_id=_ATTRACTION_A,
                chunk_key="below-threshold",
                score=0.69,
            ),
        ),
    ],
)
def test_retrieve_returns_successful_empty_result_without_evidence(
    candidates: tuple[RagV2Candidate, ...],
) -> None:
    service, embedder, _ = _service(candidates=candidates)

    result = service.retrieve(
        query="  厦门日落  ",
        dataset_key="travel-attractions-cn",
        score_threshold=0.70,
    )

    assert result == RetrievalResult(query="厦门日落", evidence=())
    assert embedder.calls == ["厦门日落"]


def test_retrieve_preserves_embedding_error_and_skips_repository() -> None:
    embedding_error = AppError(
        "RAG_V2_EMBEDDING_UNAVAILABLE",
        "RAG V2 embedding is unavailable",
    )
    embedder = FakeEmbedder(error=embedding_error)
    service, _, repository = _service(embedder=embedder)

    with pytest.raises(AppError) as raised:
        service.retrieve(query="厦门日落", dataset_key="travel-attractions-cn")

    assert raised.value is embedding_error
    assert repository.calls == []


def test_retrieve_preserves_repository_error_unchanged() -> None:
    repository_error = AppError(
        "RAG_V2_UNAVAILABLE",
        "RAG V2 persistence is unavailable",
    )
    repository = FakeRepository(error=repository_error)
    service, embedder, _ = _service(repository=repository)

    with pytest.raises(AppError) as raised:
        service.retrieve(query="厦门日落", dataset_key="travel-attractions-cn")

    assert raised.value is repository_error
    assert embedder.calls == ["厦门日落"]
