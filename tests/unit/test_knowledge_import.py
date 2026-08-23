from datetime import date
from pathlib import Path
from types import SimpleNamespace
import sys
import logging

import pytest
import httpx

from app.core.config import Settings
from app.rag.models import KnowledgeDocument
from app.rag.repository import KnowledgeRepository
from app.scripts.import_knowledge import (
    JinaEmbedder as ImportJinaEmbedder,
    KnowledgeImportService,
    StoredKnowledgeChunk,
    load_documents,
)
from app.rag.embedding import RagUnavailable


class RecordingRepository:
    def __init__(self) -> None:
        self.calls = []

    def upsert_document(self, document, chunks) -> int:
        self.calls.append((document, list(chunks)))
        return len(chunks)


class FakeEmbedder:
    def __init__(self):
        self.texts = []

    def embed(self, texts):
        self.texts.extend(texts)
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


class CapturingUpsert:
    def __init__(self, data) -> None:
        self.data = data
        self.rows = None
        self.conflict_target = None
        self.ignore_duplicates = None

    def upsert(self, rows, *, on_conflict, ignore_duplicates):
        self.rows = rows
        self.conflict_target = on_conflict
        self.ignore_duplicates = ignore_duplicates
        return self

    def execute(self):
        return SimpleNamespace(data=self.data)


class CapturingSupabaseClient:
    def __init__(self, data) -> None:
        self.query = CapturingUpsert(data)
        self.table_name = None

    def table(self, name):
        self.table_name = name
        return self.query


class RecordingQuota:
    def __init__(self, allowed=True) -> None:
        self.allowed = allowed
        self.calls = []

    def reserve(self, requested, limit):
        self.calls.append((requested, limit))
        return self.allowed


class CapturingQuotaRpc:
    def __init__(self, data) -> None:
        self.data = data
        self.name = None
        self.arguments = None

    def execute(self):
        return SimpleNamespace(data=self.data)


class CapturingQuotaClient:
    def __init__(self, data) -> None:
        self.query = CapturingQuotaRpc(data)

    def rpc(self, name, arguments):
        self.query.name = name
        self.query.arguments = arguments
        return self.query


def _repository_with_service_client(monkeypatch, response_data):
    client = CapturingSupabaseClient(response_data)
    calls = []

    def create_client(url, key):
        calls.append((url, key))
        return client

    monkeypatch.setitem(sys.modules, "supabase", SimpleNamespace(create_client=create_client))
    repository = KnowledgeRepository(
        settings=Settings(
            supabase_url="https://project.supabase.co",
            supabase_anon_key="anon-key-must-not-be-used",
            supabase_service_key="service-role-key",
        )
    )
    return repository, client, calls


def _stored_chunk() -> StoredKnowledgeChunk:
    document = sample_document()
    return StoredKnowledgeChunk(
        chunk_id="fujian-test:2026-08-12:0001",
        document_id=document.document_id,
        title=document.title,
        document_version=document.document_version,
        region=document.region,
        topic=document.topic,
        content="第一段用于验证稳定分块。",
        source_label=document.source_label,
        reviewed_on=document.reviewed_on,
        embedding=[0.0] * 1024,
    )


def test_repository_uses_service_key_and_serializes_only_migration_columns(monkeypatch):
    """Sending title or the anon key would make the real PostgREST import unsafe or fail."""
    repository, client, calls = _repository_with_service_client(monkeypatch, [{"chunk_id": "x"}])

    assert repository.upsert_document(sample_document(), [_stored_chunk()]) == 1

    assert calls == [("https://project.supabase.co/", "service-role-key")]
    assert client.table_name == "knowledge_chunks"
    assert client.query.conflict_target == "chunk_id"
    assert client.query.ignore_duplicates is True
    assert set(client.query.rows[0]) == {
        "chunk_id",
        "document_id",
        "document_version",
        "region",
        "topic",
        "content",
        "source_label",
        "reviewed_on",
        "embedding",
    }
    assert "title" not in client.query.rows[0]


def test_repository_reports_zero_rows_when_database_ignores_same_chunk_id(monkeypatch):
    """Replacing duplicate-safe upsert or its count must be observable at the repository boundary."""
    repository, client, _ = _repository_with_service_client(monkeypatch, [])

    assert repository.upsert_document(sample_document(), [_stored_chunk()]) == 0
    assert (client.query.conflict_target, client.query.ignore_duplicates) == ("chunk_id", True)


def test_repository_rejects_arbitrary_client_injection():
    """Accepting an anon client here would bypass the server-side service-role boundary."""
    with pytest.raises(TypeError):
        KnowledgeRepository(client=object())


def test_repository_requires_service_role_configuration():
    """An anon credential alone must never construct the private raw-table client."""
    with pytest.raises(RuntimeError, match="service-role"):
        KnowledgeRepository(
            settings=Settings(
                _env_file=None,
                supabase_url="https://project.supabase.co",
                supabase_anon_key="anon-key-must-not-be-used",
            )
        )


def test_repository_reserves_embedding_quota_through_private_atomic_rpc(monkeypatch):
    client = CapturingQuotaClient(True)
    monkeypatch.setitem(
        sys.modules, "supabase", SimpleNamespace(create_client=lambda _url, _key: client)
    )
    repository = KnowledgeRepository(
        settings=Settings(
            supabase_url="https://project.supabase.co",
            supabase_service_key="service-role-key",
        )
    )

    assert repository.reserve(requested=2, limit=5) is True
    assert client.query.name == "reserve_rag_embedding_quota"
    assert client.query.arguments == {
        "requested": 2,
        "daily_limit": 5,
    }


def test_repository_logs_quota_rpc_failures_without_raw_upstream_body(monkeypatch, caplog):
    class Client:
        def rpc(self, _name, _arguments):
            return SimpleNamespace(
                execute=lambda: (_ for _ in ()).throw(httpx.ConnectError("raw database body"))
            )

    monkeypatch.setitem(sys.modules, "supabase", SimpleNamespace(create_client=lambda _url, _key: Client()))
    repository = KnowledgeRepository(
        settings=Settings(
            supabase_url="https://project.supabase.co",
            supabase_service_key="service-role-key",
        )
    )

    with caplog.at_level(logging.WARNING, logger="app.database"):
        with pytest.raises(RagUnavailable):
            repository.reserve(requested=1, limit=100)

    record = next(record for record in caplog.records if record.message == "database_result")
    assert record.db_operation == "rag.embedding_quota.reserve"
    assert record.db_status == "failure"
    assert record.exception_type == "ConnectError"
    assert "raw database body" not in caplog.text


def test_repository_rejects_a_first_embedding_batch_larger_than_the_daily_limit(
    monkeypatch,
):
    client = object()
    monkeypatch.setitem(
        sys.modules, "supabase", SimpleNamespace(create_client=lambda _url, _key: client)
    )
    repository = KnowledgeRepository(
        settings=Settings(
            supabase_url="https://project.supabase.co",
            supabase_service_key="service-role-key",
        )
    )

    assert repository.reserve(requested=2, limit=1) is False


def test_import_chunks_are_stable_and_keep_document_provenance():
    """Replacing content-derived IDs or metadata propagation would corrupt retrieval rows."""
    repository = RecordingRepository()
    embedder = FakeEmbedder()
    service = KnowledgeImportService(repository, embedder)

    assert service.import_documents([sample_document()]) == 2
    chunks = repository.calls[0][1]

    assert [chunk.chunk_id for chunk in chunks] == [
        "fujian-test:2026-08-12:0001",
        "fujian-test:2026-08-12:0002",
    ]
    assert {(chunk.region, chunk.topic, chunk.source_label) for chunk in chunks} == {
        ("福建", "景点", "福建省文化和旅游厅（试点整理）")
    }
    assert embedder.texts == [
        "福建\n景点\n福建测试资料\n第一段用于验证稳定分块。",
        "福建\n景点\n福建测试资料\n第二段用于验证重复导入。",
    ]


def test_builtin_content_covers_required_topics_for_each_pilot_region():
    """Removing a region topic or its source/review date must fail the import contract."""
    fixtures = {
        "fujian.yaml": "福建",
        "yunnan.yaml": "云南",
        "xiamen.yaml": "厦门",
    }
    content_dir = Path("app/rag/content")
    assert {path.name for path in content_dir.glob("*.yaml")} == set(fixtures)
    documents = load_documents(content_dir)

    assert len(documents) == 12
    assert {(document.region, document.topic) for document in documents} == {
        (region, topic)
        for region in fixtures.values()
        for topic in ("景点", "交通", "餐饮", "季节与避坑")
    }
    assert all(document.source_label and document.reviewed_on for document in documents)


def test_import_embedder_uses_shared_jina_payload_validation(monkeypatch):
    """A response with a wrong index must fail identically to runtime retrieval."""
    transport = httpx.MockTransport(
        lambda _request: httpx.Response(
            200, json={"data": [{"index": 9, "embedding": [0.0] * 1024}]}
        )
    )
    quota = RecordingQuota()
    embedder = ImportJinaEmbedder(
        Settings(jina_api_key="test-only-placeholder", weather_timeout_seconds=4.0),
        quota=quota,
        client=httpx.Client(transport=transport),
    )

    with pytest.raises(RagUnavailable):
        embedder.embed(["导入资料"])
    assert quota.calls == [(1, 100)]


def test_import_embedder_refuses_when_authoritative_quota_is_exhausted() -> None:
    quota = RecordingQuota(allowed=False)
    transport_called = False

    def handler(_request):
        nonlocal transport_called
        transport_called = True
        return httpx.Response(200, json={"data": []})

    embedder = ImportJinaEmbedder(
        Settings(jina_api_key="test-only-placeholder", rag_daily_embedding_limit=3),
        quota=quota,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    with pytest.raises(RagUnavailable):
        embedder.embed(["第一条", "第二条"])

    assert quota.calls == [(2, 3)]
    assert transport_called is False
