from __future__ import annotations

from dataclasses import FrozenInstanceError, fields
from datetime import date
from inspect import signature
from typing import Protocol, get_type_hints
from uuid import UUID

import pytest

from app import rag_v2
from app.rag_v2.models import ChunkType
from app.rag_v2 import retrieval as retrieval_module
from app.rag_v2.retrieval import (
    QueryEmbedder,
    RetrievalEvidence,
    RetrievalResult,
)


_ATTRACTION_ID = UUID("11111111-1111-1111-1111-111111111111")


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


def test_task2_module_exposes_no_retrieval_service_or_orchestration() -> None:
    assert not hasattr(retrieval_module, "RetrievalService")
    assert not hasattr(retrieval_module, "retrieve")


def test_task2_symbols_remain_module_level_and_root_exports_stay_frozen() -> None:
    assert "QueryEmbedder" not in rag_v2.__all__
    assert "RetrievalEvidence" not in rag_v2.__all__
    assert "RetrievalResult" not in rag_v2.__all__
    assert len(rag_v2.__all__) == 44
