from datetime import date
from pathlib import Path

from app.rag.models import KnowledgeDocument
from app.scripts.import_knowledge import KnowledgeImportService, load_documents


class FakeKnowledgeRepository:
    def __init__(self) -> None:
        self.stored_chunk_ids: set[str] = set()
        self.chunks = []

    def upsert_document(self, document, chunks) -> int:
        new_chunks = [chunk for chunk in chunks if chunk.chunk_id not in self.stored_chunk_ids]
        self.stored_chunk_ids.update(chunk.chunk_id for chunk in new_chunks)
        self.chunks.extend(new_chunks)
        return len(new_chunks)


class FakeEmbedder:
    def embed(self, texts):
        return [[0.0] * 1024 for _ in texts]


def sample_document() -> KnowledgeDocument:
    return KnowledgeDocument(
        document_id="fujian-test",
        title="福建测试资料",
        document_version="2026-08-12",
        region="福建",
        topic="景点",
        source_label="福建省文化和旅游厅（试点整理）",
        content="第一段用于验证稳定分块。\n\n第二段用于验证重复导入。",
        reviewed_on=date(2026, 8, 12),
    )


def test_reimport_same_document_version_does_not_write_chunks_twice():
    """Changing stable IDs or omitting duplicate protection would insert the second run."""
    repository = FakeKnowledgeRepository()
    service = KnowledgeImportService(repository, FakeEmbedder())

    assert service.import_documents([sample_document()]) == 2
    first_chunk_ids = [chunk.chunk_id for chunk in repository.chunks]
    assert service.import_documents([sample_document()]) == 0
    assert [chunk.chunk_id for chunk in repository.chunks] == first_chunk_ids


def test_import_chunks_are_stable_and_keep_document_provenance():
    """Replacing content-derived IDs or metadata propagation would corrupt retrieval rows."""
    repository = FakeKnowledgeRepository()
    service = KnowledgeImportService(repository, FakeEmbedder())

    assert service.import_documents([sample_document()]) == 2

    assert [chunk.chunk_id for chunk in repository.chunks] == [
        "fujian-test:2026-08-12:0001",
        "fujian-test:2026-08-12:0002",
    ]
    assert {(chunk.region, chunk.topic, chunk.source_label) for chunk in repository.chunks} == {
        ("福建", "景点", "福建省文化和旅游厅（试点整理）")
    }


def test_builtin_content_covers_required_topics_for_each_pilot_region():
    """Removing a region topic or its source/review date must fail the import contract."""
    fixtures = {
        "fujian.yaml": "福建",
        "yunnan.yaml": "云南",
        "xiamen.yaml": "厦门",
    }
    documents = load_documents(Path("app/rag/content"))

    assert {(document.region, document.topic) for document in documents} == {
        (region, topic)
        for region in fixtures.values()
        for topic in ("景点", "交通", "餐饮", "季节与避坑")
    }
    assert all(document.source_label and document.reviewed_on for document in documents)
