from datetime import date
from uuid import UUID

import pytest

from app.core.errors import AppError
from app.rag_v2.knowledge import RagV2KnowledgeAdapter, V2KnowledgeResult
from app.rag_v2.models import ChunkType, DestinationLevel
from app.rag_v2.retrieval import RetrievalEvidence, RetrievalResult


_FIRST_ATTRACTION_ID = UUID("00000000-0000-4000-8000-000000000101")
_SECOND_ATTRACTION_ID = UUID("00000000-0000-4000-8000-000000000102")


class FakeRetrievalService:
    def __init__(self, result: RetrievalResult) -> None:
        self.result = result
        self.calls: list[dict[str, object]] = []

    def retrieve(self, **kwargs: object) -> RetrievalResult:
        self.calls.append(kwargs)
        return self.result


class RaisingRetrievalService:
    def __init__(self, error: AppError) -> None:
        self.error = error

    def retrieve(self, **kwargs: object) -> RetrievalResult:
        raise self.error


def _task2_evidence(**overrides: object) -> RetrievalEvidence:
    values: dict[str, object] = {
        "attraction_id": _FIRST_ATTRACTION_ID,
        "chunk_key": "rag-v2-chunk-key-v1|00000000-0000-4000-8000-000000000101|overview|0",
        "chunk_type": ChunkType.overview,
        "content": "鼓浪屿以历史街区和海岛步行为主。",
        "content_hash": "hash-task2",
        "source_label": "厦门市文化和旅游局",
        "source_url": "https://wlj.xm.gov.cn/",
        "source_type": "official",
        "reviewed_on": date(2026, 8, 1),
        "score": 0.72,
    }
    values.update(overrides)
    return RetrievalEvidence(**values)


def test_v2_adapter_returns_ordered_evidence_only_content_and_fixed_dataset() -> None:
    first = RetrievalEvidence(
        attraction_id=_FIRST_ATTRACTION_ID,
        chunk_key="rag-v2-chunk-key-v1|00000000-0000-4000-8000-000000000101|overview|0",
        chunk_type=ChunkType.overview,
        content="鼓浪屿以历史街区和海岛步行为主。",
        content_hash="hash-1",
        source_label="厦门市文化和旅游局",
        source_url="https://wlj.xm.gov.cn/",
        source_type="official",
        reviewed_on=date(2026, 8, 1),
        score=0.72,
    )
    second = RetrievalEvidence(
        attraction_id=_SECOND_ATTRACTION_ID,
        chunk_key="rag-v2-chunk-key-v1|00000000-0000-4000-8000-000000000102|highlights|0",
        chunk_type=ChunkType.highlights,
        content="建筑街巷和海岸步道适合分段步行体验。",
        content_hash="hash-2",
        source_label="厦门市文化和旅游局",
        source_url="https://wlj.xm.gov.cn/",
        source_type="official",
        reviewed_on=date(2026, 8, 1),
        score=0.68,
    )
    evidence = (first, second)
    retrieval = FakeRetrievalService(
        RetrievalResult(query="原始 query", evidence=evidence)
    )

    result = RagV2KnowledgeAdapter(retrieval=retrieval).answer(
        "鼓浪屿有哪些特点？"
    )

    assert isinstance(result, V2KnowledgeResult)
    assert result.status == "grounded"
    assert result.reply == f"{first.content}\n\n{second.content}"
    assert result.evidence == evidence
    assert [
        (item.source_label, item.source_url, item.source_type)
        for item in result.evidence
    ] == [
        ("厦门市文化和旅游局", "https://wlj.xm.gov.cn/", "official"),
        ("厦门市文化和旅游局", "https://wlj.xm.gov.cn/", "official"),
    ]
    assert str(_FIRST_ATTRACTION_ID) not in result.reply
    assert first.chunk_key not in result.reply

    assert retrieval.calls == [
        {
            "query": "鼓浪屿有哪些特点？",
            "dataset_key": "rag-v2-production",
            "destination_code": None,
            "destination_level": None,
            "province_code": None,
            "attraction_id": None,
        }
    ]
    assert "candidate_k" not in retrieval.calls[0]
    assert "final_k" not in retrieval.calls[0]
    assert "score_threshold" not in retrieval.calls[0]


def test_v2_adapter_returns_empty_status_for_empty_retrieval_result() -> None:
    retrieval = FakeRetrievalService(
        RetrievalResult(query="没有命中", evidence=())
    )

    result = RagV2KnowledgeAdapter(retrieval=retrieval).answer("没有命中")

    assert result == V2KnowledgeResult(
        status="empty",
        reply=None,
        evidence=(),
        error_code=None,
    )


def test_v2_adapter_normalizes_embedding_unavailable() -> None:
    retrieval = RaisingRetrievalService(
        AppError("RAG_V2_EMBEDDING_UNAVAILABLE", "provider detail must not escape")
    )

    result = RagV2KnowledgeAdapter(retrieval=retrieval).answer("鼓浪屿有什么特点？")

    assert result == V2KnowledgeResult(
        status="unavailable",
        reply=None,
        evidence=(),
        error_code="RAG_V2_EMBEDDING_UNAVAILABLE",
    )


def test_v2_adapter_normalizes_repository_unavailable() -> None:
    retrieval = RaisingRetrievalService(
        AppError("RAG_V2_UNAVAILABLE", "database detail must not escape")
    )

    result = RagV2KnowledgeAdapter(retrieval=retrieval).answer("鼓浪屿有什么特点？")

    assert result == V2KnowledgeResult(
        status="unavailable",
        reply=None,
        evidence=(),
        error_code="RAG_V2_UNAVAILABLE",
    )


def test_v2_adapter_does_not_swallow_unclassified_app_error() -> None:
    error = AppError("RAG_V2_NOT_FOUND", "unexpected detail")
    retrieval = RaisingRetrievalService(error)

    with pytest.raises(AppError) as raised:
        RagV2KnowledgeAdapter(retrieval=retrieval).answer("鼓浪屿有什么特点？")

    assert raised.value is error


def test_v2_adapter_marks_malformed_evidence_unavailable() -> None:
    malformed = _task2_evidence(source_url="http://wlj.xm.gov.cn/")
    retrieval = FakeRetrievalService(
        RetrievalResult(query="鼓浪屿有什么特点？", evidence=(malformed,))
    )

    result = RagV2KnowledgeAdapter(retrieval=retrieval).answer("鼓浪屿有什么特点？")

    assert result == V2KnowledgeResult(
        status="unavailable",
        reply=None,
        evidence=(),
        error_code="RAG_V2_MALFORMED_RESULT",
    )


def test_v2_adapter_marks_invalid_source_type_unavailable() -> None:
    malformed = _task2_evidence(source_type="untrusted")
    retrieval = FakeRetrievalService(
        RetrievalResult(query="鼓浪屿有什么特点？", evidence=(malformed,))
    )

    result = RagV2KnowledgeAdapter(retrieval=retrieval).answer("鼓浪屿有什么特点？")

    assert result == V2KnowledgeResult(
        status="unavailable",
        reply=None,
        evidence=(),
        error_code="RAG_V2_MALFORMED_RESULT",
    )


def test_v2_adapter_marks_empty_content_unavailable() -> None:
    malformed = _task2_evidence(content="")
    retrieval = FakeRetrievalService(
        RetrievalResult(query="鼓浪屿有什么特点？", evidence=(malformed,))
    )

    result = RagV2KnowledgeAdapter(retrieval=retrieval).answer("鼓浪屿有什么特点？")

    assert result == V2KnowledgeResult(
        status="unavailable",
        reply=None,
        evidence=(),
        error_code="RAG_V2_MALFORMED_RESULT",
    )


def test_missing_destination_metadata_is_forwarded_as_none_without_guessing() -> None:
    retrieval = FakeRetrievalService(
        RetrievalResult(query="厦门鼓浪屿怎么去？", evidence=())
    )

    RagV2KnowledgeAdapter(retrieval=retrieval).answer("厦门鼓浪屿怎么去？")

    assert retrieval.calls == [
        {
            "query": "厦门鼓浪屿怎么去？",
            "dataset_key": "rag-v2-production",
            "destination_code": None,
            "destination_level": None,
            "province_code": None,
            "attraction_id": None,
        }
    ]


def test_trusted_destination_filters_are_forwarded_unchanged() -> None:
    retrieval = FakeRetrievalService(
        RetrievalResult(query="鼓浪屿有什么特点？", evidence=())
    )
    attraction_id = _FIRST_ATTRACTION_ID

    RagV2KnowledgeAdapter(retrieval=retrieval).answer(
        "鼓浪屿有什么特点？",
        destination_code="350200",
        destination_level=DestinationLevel.prefecture_city,
        province_code="350000",
        attraction_id=attraction_id,
    )

    assert retrieval.calls == [
        {
            "query": "鼓浪屿有什么特点？",
            "dataset_key": "rag-v2-production",
            "destination_code": "350200",
            "destination_level": DestinationLevel.prefecture_city,
            "province_code": "350000",
            "attraction_id": attraction_id,
        }
    ]
