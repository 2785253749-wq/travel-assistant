from dataclasses import FrozenInstanceError, fields, is_dataclass
from datetime import UTC, date, datetime
from importlib import import_module
import logging
import math
import sys
from types import SimpleNamespace
from typing import get_type_hints
from uuid import UUID

import httpx
import pytest
from postgrest import ReturnMethod
from postgrest.exceptions import APIError

from app.core.config import Settings
from app.core.errors import AppError
from app.rag_v2.models import (
    AttractionVersionMetadata,
    AttractionVersionStatus,
    ChunkStatus,
    ChunkType,
    Destination,
    DestinationLevel,
    SemanticChunk,
    StableAttraction,
)


DATASET_KEY = "travel-attractions-cn"
VERSION_LABEL = "2026-09-01"
MANIFEST_HASH = "a" * 64
OTHER_MANIFEST_HASH = "b" * 64
CALLER_CORPUS_ID = UUID("11111111-1111-1111-1111-111111111111")
WINNING_CORPUS_ID = UUID("22222222-2222-2222-2222-222222222222")
ATTRACTION_ID = UUID("33333333-3333-3333-3333-333333333333")
MERGED_INTO_ID = UUID("44444444-4444-4444-4444-444444444444")
CREATED_AT = datetime(2026, 9, 1, 8, 30, tzinfo=UTC)
RETIRED_AT = datetime(2026, 9, 1, 9, 30, tzinfo=UTC)


def _repository_module():
    """Import lazily so the first RED is the missing repository module."""
    return import_module("app.rag_v2.repository")


def _repository(client):
    return _repository_module().RagV2Repository(client=client)


class FakeQuery:
    def __init__(self, client, table_name):
        self.client = client
        self.table_name = table_name
        self.operation = None
        self.selected = None
        self.payload = None
        self.filters = []

    def select(self, columns):
        if self.operation is None:
            self.operation = "select"
        self.selected = columns
        return self

    def eq(self, column, value):
        self.filters.append((column, value))
        return self

    def insert(self, payload, *, returning=None):
        self.operation = "insert"
        self.payload = payload
        self.returning = returning
        return self

    def update(self, payload):
        self.operation = "update"
        self.payload = payload
        return self

    def limit(self, _count):
        return self

    def execute(self):
        self.client.calls.append(
            {
                "table": self.table_name,
                "operation": self.operation,
                "selected": self.selected,
                "returning": getattr(self, "returning", None),
                "payload": self.payload,
                "filters": tuple(self.filters),
            }
        )
        if not self.client.responses:
            raise AssertionError("fake client response queue is exhausted")
        response = self.client.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return SimpleNamespace(data=response)


class FakeClient:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def table(self, name):
        return FakeQuery(self, name)


def _corpus_row(
    *,
    corpus_version_id=WINNING_CORPUS_ID,
    manifest_hash=MANIFEST_HASH,
    status="staging",
):
    return {
        "corpus_version_id": str(corpus_version_id),
        "dataset_key": DATASET_KEY,
        "version_label": VERSION_LABEL,
        "manifest_hash": manifest_hash,
        "status": status,
        "created_at": "2026-09-01T08:30:00+00:00",
        "activated_at": (
            "2026-09-01T09:00:00+00:00" if status == "active" else None
        ),
        "superseded_at": (
            "2026-09-01T10:00:00+00:00" if status == "superseded" else None
        ),
    }


def _attraction_row(
    *,
    lifecycle_status="active",
    retired_at=None,
    merged_into_attraction_id=None,
):
    return {
        "attraction_id": str(ATTRACTION_ID),
        "lifecycle_status": lifecycle_status,
        "created_at": "2026-09-01T08:30:00+00:00",
        "retired_at": retired_at,
        "merged_into_attraction_id": merged_into_attraction_id,
    }


def _active_attraction():
    return StableAttraction(
        attraction_id=ATTRACTION_ID,
        lifecycle_status="active",
        created_at=CREATED_AT,
    )


def _assert_app_error(error_info, code, message):
    assert error_info.value.code == code
    assert error_info.value.message == message


def test_rag_v2_repository_module_exists():
    module = _repository_module()

    assert hasattr(module, "RagV2Repository")


def test_repository_exposes_only_the_task6_api_and_keeps_escape_hatches_private():
    repository = _repository(FakeClient())
    public_names = {name for name in dir(repository) if not name.startswith("_")}

    assert {
        "create_corpus_version",
        "get_corpus_version",
        "mark_corpus_failed",
        "get_attraction",
        "insert_attraction",
        "update_attraction_lifecycle",
    } <= public_names
    for name in (
        "execute_sql",
        "raw_client",
        "table",
        "client",
        "supabase",
        "generic_upsert",
        "generic_insert",
        "rpc",
    ):
        assert not hasattr(repository, name)


def test_injected_client_is_private_and_does_not_require_environment():
    client = FakeClient()
    repository = _repository(client)

    assert repository._client is client
    assert not hasattr(repository, "raw_client")


def test_production_construction_uses_service_key_not_anon_key(monkeypatch):
    calls = []
    client = FakeClient()

    def create_client(url, key):
        calls.append((url, key))
        return client

    monkeypatch.setitem(
        sys.modules,
        "supabase",
        SimpleNamespace(create_client=create_client),
    )

    _repository_module().RagV2Repository(
        settings=Settings(
            supabase_url="https://project.supabase.co",
            supabase_anon_key="anon-key-must-not-be-used",
            supabase_service_key="service-role-key",
            _env_file=None,
        )
    )

    assert calls == [("https://project.supabase.co/", "service-role-key")]


def test_missing_service_role_configuration_fails_before_client_construction(monkeypatch):
    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("client construction must not be attempted")

    monkeypatch.setitem(
        sys.modules,
        "supabase",
        SimpleNamespace(create_client=fail_if_called),
    )

    with pytest.raises(RuntimeError, match="service-role"):
        _repository_module().RagV2Repository(
            settings=Settings(
                supabase_url="https://project.supabase.co",
                supabase_anon_key="anon-key",
                _env_file=None,
            )
        )


def test_corpus_version_is_exact_frozen_dataclass():
    corpus_type = _repository_module().CorpusVersion
    instance = corpus_type(
        corpus_version_id=WINNING_CORPUS_ID,
        dataset_key=DATASET_KEY,
        version_label=VERSION_LABEL,
        manifest_hash=MANIFEST_HASH,
        status="staging",
        created_at=CREATED_AT,
        activated_at=None,
        superseded_at=None,
    )

    assert is_dataclass(corpus_type)
    assert [field.name for field in fields(corpus_type)] == [
        "corpus_version_id",
        "dataset_key",
        "version_label",
        "manifest_hash",
        "status",
        "created_at",
        "activated_at",
        "superseded_at",
    ]
    annotations = get_type_hints(corpus_type)
    assert annotations["corpus_version_id"] is UUID
    assert annotations["dataset_key"] is str
    assert annotations["version_label"] is str
    assert annotations["manifest_hash"] is str
    assert annotations["created_at"] is datetime
    assert annotations["activated_at"] == datetime | None
    assert annotations["superseded_at"] == datetime | None
    with pytest.raises(FrozenInstanceError):
        instance.status = "active"


def test_create_corpus_version_inserts_caller_uuid_as_new_staging_row():
    row = _corpus_row(corpus_version_id=CALLER_CORPUS_ID)
    client = FakeClient([], [row])
    repository = _repository(client)

    result = repository.create_corpus_version(
        corpus_version_id=CALLER_CORPUS_ID,
        dataset_key=DATASET_KEY,
        version_label=VERSION_LABEL,
        manifest_hash=MANIFEST_HASH,
    )

    assert result.corpus_version_id == CALLER_CORPUS_ID
    assert result.status == "staging"
    select_call, insert_call = client.calls
    assert select_call["table"] == "rag_corpus_versions"
    assert select_call["operation"] == "select"
    assert select_call["filters"] == (
        ("dataset_key", DATASET_KEY),
        ("version_label", VERSION_LABEL),
    )
    assert insert_call["table"] == "rag_corpus_versions"
    assert insert_call["operation"] == "insert"
    assert insert_call["payload"] == {
        "corpus_version_id": str(CALLER_CORPUS_ID),
        "dataset_key": DATASET_KEY,
        "version_label": VERSION_LABEL,
        "manifest_hash": MANIFEST_HASH,
        "status": "staging",
    }


@pytest.mark.parametrize("status", ["staging", "active", "superseded"])
def test_same_hash_reuses_authoritative_corpus_for_reusable_statuses(status):
    client = FakeClient([_corpus_row(status=status)])
    repository = _repository(client)

    result = repository.create_corpus_version(
        corpus_version_id=CALLER_CORPUS_ID,
        dataset_key=DATASET_KEY,
        version_label=VERSION_LABEL,
        manifest_hash=MANIFEST_HASH,
    )

    assert result.corpus_version_id == WINNING_CORPUS_ID
    assert len(client.calls) == 1
    assert all(call["operation"] != "insert" for call in client.calls)


@pytest.mark.parametrize(
    "existing_row",
    [
        _corpus_row(status="failed"),
        _corpus_row(manifest_hash=OTHER_MANIFEST_HASH),
    ],
    ids=["same-hash-failed", "different-hash"],
)
def test_existing_corpus_conflicts_are_not_replaced(existing_row):
    client = FakeClient([existing_row])
    repository = _repository(client)

    with pytest.raises(AppError) as error:
        repository.create_corpus_version(
            corpus_version_id=CALLER_CORPUS_ID,
            dataset_key=DATASET_KEY,
            version_label=VERSION_LABEL,
            manifest_hash=MANIFEST_HASH,
        )

    _assert_app_error(
        error,
        "RAG_V2_VERSION_CONFLICT",
        "RAG V2 corpus version conflicts with existing data",
    )
    assert len(client.calls) == 1
    assert all(call["operation"] != "insert" for call in client.calls)


@pytest.mark.parametrize("status", ["staging", "active", "superseded"])
def test_unique_race_re_reads_exact_identity_and_returns_winning_row(status):
    client = FakeClient(
        [],
        APIError(
            {
                "code": "23505",
                "message": "duplicate key value violates unique constraint",
            }
        ),
        [_corpus_row(status=status)],
    )
    repository = _repository(client)

    result = repository.create_corpus_version(
        corpus_version_id=CALLER_CORPUS_ID,
        dataset_key=DATASET_KEY,
        version_label=VERSION_LABEL,
        manifest_hash=MANIFEST_HASH,
    )

    assert result.corpus_version_id == WINNING_CORPUS_ID
    select_calls = [call for call in client.calls if call["operation"] == "select"]
    assert len(select_calls) == 2
    assert select_calls[0]["filters"] == select_calls[1]["filters"] == (
        ("dataset_key", DATASET_KEY),
        ("version_label", VERSION_LABEL),
    )
    insert_call = next(call for call in client.calls if call["operation"] == "insert")
    assert insert_call["payload"]["corpus_version_id"] == str(CALLER_CORPUS_ID)


@pytest.mark.parametrize(
    "winner",
    [
        _corpus_row(status="failed"),
        _corpus_row(manifest_hash=OTHER_MANIFEST_HASH),
    ],
    ids=["failed-winner", "different-hash-winner"],
)
def test_unique_race_conflict_winner_uses_same_frozen_matrix(winner):
    client = FakeClient(
        [],
        APIError({"code": "23505", "message": "duplicate key"}),
        [winner],
    )
    repository = _repository(client)

    with pytest.raises(AppError) as error:
        repository.create_corpus_version(
            corpus_version_id=CALLER_CORPUS_ID,
            dataset_key=DATASET_KEY,
            version_label=VERSION_LABEL,
            manifest_hash=MANIFEST_HASH,
        )

    _assert_app_error(
        error,
        "RAG_V2_VERSION_CONFLICT",
        "RAG V2 corpus version conflicts with existing data",
    )
    assert len([call for call in client.calls if call["operation"] == "select"]) == 2


def test_unique_race_without_authoritative_row_is_unavailable():
    client = FakeClient(
        [],
        APIError({"code": "23505", "message": "duplicate key"}),
        [],
    )
    repository = _repository(client)

    with pytest.raises(AppError) as error:
        repository.create_corpus_version(
            corpus_version_id=CALLER_CORPUS_ID,
            dataset_key=DATASET_KEY,
            version_label=VERSION_LABEL,
            manifest_hash=MANIFEST_HASH,
        )

    _assert_app_error(error, "RAG_V2_UNAVAILABLE", "RAG V2 persistence is unavailable")
    assert len([call for call in client.calls if call["operation"] == "select"]) == 2
    assert len([call for call in client.calls if call["operation"] == "insert"]) == 1


def test_only_structured_23505_enters_unique_race_recovery():
    client = FakeClient(
        [],
        APIError({"code": "400", "message": "duplicate-looking upstream text"}),
    )
    repository = _repository(client)

    with pytest.raises(AppError) as error:
        repository.create_corpus_version(
            corpus_version_id=CALLER_CORPUS_ID,
            dataset_key=DATASET_KEY,
            version_label=VERSION_LABEL,
            manifest_hash=MANIFEST_HASH,
        )

    _assert_app_error(error, "RAG_V2_UNAVAILABLE", "RAG V2 persistence is unavailable")
    assert len([call for call in client.calls if call["operation"] == "select"]) == 1


@pytest.mark.parametrize("response", [[_corpus_row()], []], ids=["found", "missing"])
def test_get_corpus_version_decodes_or_returns_none(response):
    client = FakeClient(response)
    result = _repository(client).get_corpus_version(corpus_version_id=WINNING_CORPUS_ID)

    if response:
        assert result.corpus_version_id == WINNING_CORPUS_ID
        assert result.created_at.tzinfo is not None
    else:
        assert result is None
    assert client.calls[0]["table"] == "rag_corpus_versions"
    assert client.calls[0]["filters"] == (("corpus_version_id", str(WINNING_CORPUS_ID)),)


def test_malformed_corpus_row_is_unavailable():
    client = FakeClient([{"corpus_version_id": str(WINNING_CORPUS_ID)}])

    with pytest.raises(AppError) as error:
        _repository(client).get_corpus_version(corpus_version_id=WINNING_CORPUS_ID)

    _assert_app_error(error, "RAG_V2_UNAVAILABLE", "RAG V2 persistence is unavailable")


def test_mark_corpus_failed_updates_only_lifecycle_and_maps_missing_target():
    row = _corpus_row(status="failed")
    client = FakeClient([row])

    result = _repository(client).mark_corpus_failed(corpus_version_id=WINNING_CORPUS_ID)

    assert result.status == "failed"
    call = client.calls[0]
    assert call["table"] == "rag_corpus_versions"
    assert call["operation"] == "update"
    assert call["filters"] == (("corpus_version_id", str(WINNING_CORPUS_ID)),)
    assert call["payload"] == {"status": "failed"}

    missing_client = FakeClient([])
    with pytest.raises(AppError) as error:
        _repository(missing_client).mark_corpus_failed(
            corpus_version_id=WINNING_CORPUS_ID
        )
    _assert_app_error(error, "RAG_V2_NOT_FOUND", "RAG V2 record not found")


def test_lifecycle_database_guard_maps_only_known_lifecycle_error():
    client = FakeClient(
        APIError(
            {
                "code": "P0001",
                "message": "RAG V2 corpus lifecycle transition is invalid",
            }
        )
    )

    with pytest.raises(AppError) as error:
        _repository(client).mark_corpus_failed(corpus_version_id=WINNING_CORPUS_ID)

    _assert_app_error(
        error,
        "RAG_V2_INVALID_LIFECYCLE",
        "RAG V2 lifecycle transition is invalid",
    )


def test_unexpected_database_failure_is_unavailable_and_private(caplog):
    client = FakeClient(httpx.ConnectError("raw response body service-secret"))

    with caplog.at_level(logging.WARNING, logger="app.database"):
        with pytest.raises(AppError) as error:
            _repository(client).get_attraction(attraction_id=ATTRACTION_ID)

    _assert_app_error(error, "RAG_V2_UNAVAILABLE", "RAG V2 persistence is unavailable")
    assert "raw response body" not in str(error.value)
    assert "service-secret" not in caplog.text


def test_get_attraction_decodes_valid_row_or_returns_none():
    valid_client = FakeClient([_attraction_row()])
    result = _repository(valid_client).get_attraction(attraction_id=ATTRACTION_ID)

    assert isinstance(result, StableAttraction)
    assert result.attraction_id == ATTRACTION_ID
    assert result.created_at.tzinfo is not None
    assert valid_client.calls[0]["filters"] == (("attraction_id", str(ATTRACTION_ID)),)

    missing_client = FakeClient([])
    assert _repository(missing_client).get_attraction(attraction_id=ATTRACTION_ID) is None


def test_malformed_attraction_row_is_unavailable():
    client = FakeClient([{"attraction_id": str(ATTRACTION_ID)}])

    with pytest.raises(AppError) as error:
        _repository(client).get_attraction(attraction_id=ATTRACTION_ID)

    _assert_app_error(error, "RAG_V2_UNAVAILABLE", "RAG V2 persistence is unavailable")


def test_insert_attraction_serializes_only_stable_fields():
    attraction = _active_attraction()
    client = FakeClient([_attraction_row()])

    result = _repository(client).insert_attraction(attraction)

    assert result.attraction_id == ATTRACTION_ID
    call = client.calls[0]
    assert call["table"] == "rag_attractions"
    assert call["operation"] == "insert"
    assert set(call["payload"]) == {
        "attraction_id",
        "lifecycle_status",
        "created_at",
        "retired_at",
        "merged_into_attraction_id",
    }
    assert call["payload"]["attraction_id"] == str(ATTRACTION_ID)
    assert call["payload"]["created_at"] is not None
    assert all(
        field not in call["payload"]
        for field in (
            "canonical_name",
            "aliases",
            "destination",
            "province",
            "district",
            "category",
            "tags",
            "coordinates",
            "provenance",
            "metadata_hash",
        )
    )


@pytest.mark.parametrize(
    ("lifecycle_status", "retired_at", "merged_into_attraction_id", "row"),
    [
        ("retired", RETIRED_AT, None, _attraction_row(lifecycle_status="retired", retired_at="2026-09-01T09:30:00+00:00")),
        ("merged", None, MERGED_INTO_ID, _attraction_row(lifecycle_status="merged", merged_into_attraction_id=str(MERGED_INTO_ID))),
    ],
    ids=["retire", "merge"],
)
def test_update_attraction_lifecycle_sends_only_approved_lifecycle_fields(
    lifecycle_status, retired_at, merged_into_attraction_id, row
):
    client = FakeClient([row])

    result = _repository(client).update_attraction_lifecycle(
        attraction_id=ATTRACTION_ID,
        lifecycle_status=lifecycle_status,
        retired_at=retired_at,
        merged_into_attraction_id=merged_into_attraction_id,
    )

    assert result.lifecycle_status.value == lifecycle_status
    call = client.calls[0]
    assert call["table"] == "rag_attractions"
    assert call["operation"] == "update"
    assert call["filters"] == (("attraction_id", str(ATTRACTION_ID)),)
    assert set(call["payload"]) <= {
        "lifecycle_status",
        "retired_at",
        "merged_into_attraction_id",
    }
    assert call["payload"]["lifecycle_status"] == lifecycle_status
    if retired_at is not None:
        assert call["payload"]["retired_at"]
    if merged_into_attraction_id is not None:
        assert call["payload"]["merged_into_attraction_id"] == str(MERGED_INTO_ID)


TASK7_CORPUS_ID = UUID("55555555-5555-5555-5555-555555555555")
TASK7_ATTRACTION_A = UUID("66666666-6666-6666-6666-666666666666")
TASK7_ATTRACTION_B = UUID("77777777-7777-7777-7777-777777777777")
TASK7_ATTRACTION_C = UUID("88888888-8888-8888-8888-888888888888")
TASK7_REVIEWED_ON = date(2026, 9, 1)
TASK7_VECTOR = [0.1] * 1024
TASK7_VECTOR_TEXT = "[" + ",".join("0.1" for _ in range(1024)) + "]"


def _task7_metadata(
    *,
    attraction_id=TASK7_ATTRACTION_A,
    canonical_name="Task 7 attraction",
):
    return AttractionVersionMetadata(
        attraction_id=attraction_id,
        canonical_name=canonical_name,
        aliases=("Alias one", "Alias two"),
        destination=Destination(
            destination_code="350200",
            destination_level=DestinationLevel.prefecture_city,
            destination_name="Xiamen",
            province_code="350000",
            province_name="Fujian",
            district_name="思明区",
            latitude=24.4798,
            longitude=118.0894,
        ),
        category="scenic",
        tags=("coast", "city"),
        status=AttractionVersionStatus.included,
    )


def _task7_version_record(*, attraction_id=TASK7_ATTRACTION_A, canonical_name=None):
    module = _repository_module()
    return module.AttractionVersionRecord(
        corpus_version_id=TASK7_CORPUS_ID,
        metadata=_task7_metadata(
            attraction_id=attraction_id,
            canonical_name=canonical_name or "Task 7 attraction",
        ),
        metadata_hash="c" * 64,
    )


def _task7_chunk(*, attraction_id=TASK7_ATTRACTION_A, chunk_key="chunk-a"):
    return SemanticChunk(
        chunk_key=chunk_key,
        attraction_id=attraction_id,
        chunk_type=ChunkType.overview,
        ordinal=0,
        normalized_content="Normalized task 7 content.",
        content_hash="d" * 64,
        embedding_input_hash="e" * 64,
        source_label="official source",
        source_url="https://example.com/task7",
        source_type="official",
        reviewed_on=TASK7_REVIEWED_ON,
    )


def _task7_chunk_insert(*, attraction_id=TASK7_ATTRACTION_A, chunk_key="chunk-a"):
    module = _repository_module()
    return module.ChunkInsert(
        corpus_version_id=TASK7_CORPUS_ID,
        chunk=_task7_chunk(attraction_id=attraction_id, chunk_key=chunk_key),
    )


def _task7_version_row(record, *, canonical_name=None):
    metadata = record.metadata
    destination = metadata.destination
    return {
        "corpus_version_id": str(record.corpus_version_id),
        "attraction_id": str(metadata.attraction_id),
        "canonical_name": canonical_name or metadata.canonical_name,
        "aliases": list(metadata.aliases),
        "destination_code": destination.destination_code,
        "destination_level": destination.destination_level.value,
        "destination_name": destination.destination_name,
        "province_code": destination.province_code,
        "province_name": destination.province_name,
        "district_name": destination.district_name,
        "category": metadata.category,
        "tags": list(metadata.tags),
        "latitude": destination.latitude,
        "longitude": destination.longitude,
        "status": metadata.status.value,
        "metadata_hash": record.metadata_hash,
    }


def _task7_chunk_row(
    chunk,
    *,
    embedding=None,
    status="pending",
    embedding_error_code=None,
    embedding_error_message=None,
    content=None,
):
    return {
        "corpus_version_id": str(TASK7_CORPUS_ID),
        "attraction_id": str(chunk.attraction_id),
        "chunk_key": chunk.chunk_key,
        "chunk_type": chunk.chunk_type.value,
        "ordinal": chunk.ordinal,
        "content": content or chunk.normalized_content,
        "content_hash": chunk.content_hash,
        "embedding_input_hash": chunk.embedding_input_hash,
        "embedding_input_schema_version": "rag-v2-embedding-input-v1",
        "source_label": chunk.source_label,
        "source_url": chunk.source_url,
        "source_type": chunk.source_type,
        "reviewed_on": chunk.reviewed_on.isoformat(),
        "embedding_model": "jina-embeddings-v3",
        "embedding_task": "retrieval.passage",
        "embedding_dimensions": 1024,
        "embedding": embedding,
        "status": status,
        "embedding_error_code": embedding_error_code,
        "embedding_error_message": embedding_error_message,
    }


def _assert_task7_error(error_info, code, message):
    _assert_app_error(error_info, code, message)


def test_attraction_version_record_is_exact_frozen_dataclass():
    module = _repository_module()
    record_type = module.AttractionVersionRecord
    instance = record_type(
        corpus_version_id=TASK7_CORPUS_ID,
        metadata=_task7_metadata(),
        metadata_hash="c" * 64,
    )

    assert is_dataclass(record_type)
    assert [field.name for field in fields(record_type)] == [
        "corpus_version_id",
        "metadata",
        "metadata_hash",
    ]
    annotations = get_type_hints(record_type)
    assert annotations["corpus_version_id"] is UUID
    assert annotations["metadata"] is AttractionVersionMetadata
    assert annotations["metadata_hash"] is str
    with pytest.raises(FrozenInstanceError):
        instance.metadata_hash = "f" * 64


def test_chunk_insert_is_exact_frozen_dataclass_without_persistence_fields():
    module = _repository_module()
    chunk_type = module.ChunkInsert
    instance = chunk_type(corpus_version_id=TASK7_CORPUS_ID, chunk=_task7_chunk())

    assert is_dataclass(chunk_type)
    assert [field.name for field in fields(chunk_type)] == [
        "corpus_version_id",
        "chunk",
    ]
    annotations = get_type_hints(chunk_type)
    assert annotations["corpus_version_id"] is UUID
    assert annotations["chunk"] is SemanticChunk
    with pytest.raises(FrozenInstanceError):
        instance.chunk = _task7_chunk(chunk_key="replacement")
    assert not hasattr(instance, "embedding")
    assert not hasattr(instance, "status")
    assert not hasattr(instance, "error_code")


def test_chunk_row_is_exact_frozen_dataclass_with_immutable_vector():
    module = _repository_module()
    row_type = module.ChunkRow
    instance = row_type(
        corpus_version_id=TASK7_CORPUS_ID,
        attraction_id=TASK7_ATTRACTION_A,
        chunk_key="chunk-a",
        chunk_type=ChunkType.overview,
        ordinal=0,
        content="content",
        content_hash="d" * 64,
        embedding_input_hash="e" * 64,
        embedding_input_schema_version="rag-v2-embedding-input-v1",
        source_label="source",
        source_url="https://example.com",
        source_type="official",
        reviewed_on=TASK7_REVIEWED_ON,
        embedding_model="jina-embeddings-v3",
        embedding_task="retrieval.passage",
        embedding_dimensions=1024,
        embedding=(0.1,) * 1024,
        status=ChunkStatus.embedded,
        embedding_error_code=None,
        embedding_error_message=None,
    )

    assert is_dataclass(row_type)
    assert [field.name for field in fields(row_type)] == [
        "corpus_version_id",
        "attraction_id",
        "chunk_key",
        "chunk_type",
        "ordinal",
        "content",
        "content_hash",
        "embedding_input_hash",
        "embedding_input_schema_version",
        "source_label",
        "source_url",
        "source_type",
        "reviewed_on",
        "embedding_model",
        "embedding_task",
        "embedding_dimensions",
        "embedding",
        "status",
        "embedding_error_code",
        "embedding_error_message",
    ]
    annotations = get_type_hints(row_type)
    assert annotations["corpus_version_id"] is UUID
    assert annotations["attraction_id"] is UUID
    assert annotations["chunk_type"] is ChunkType
    assert annotations["reviewed_on"] is date
    assert annotations["embedding"] == tuple[float, ...] | None
    assert annotations["status"] is ChunkStatus
    with pytest.raises(FrozenInstanceError):
        instance.status = ChunkStatus.failed


def test_task7_methods_are_present_without_task8_or_reuse_surface():
    repository = _repository(FakeClient())
    public_names = {name for name in dir(repository) if not name.startswith("_")}

    assert {
        "insert_attraction_versions",
        "insert_chunks",
        "mark_chunk_embedded",
        "mark_chunk_embedding_failed",
        "reset_chunk_embedding_for_retry",
    } <= public_names
    for name in (
        "activate_corpus",
        "match_chunks",
        "upsert",
        "generic_insert",
        "generic_update",
        "delete_chunk",
        "update_chunk_content",
        "update_attraction_version",
        "raw_client",
        "execute_sql",
        "table",
        "rpc",
    ):
        assert not hasattr(repository, name)


def test_empty_version_batch_returns_empty_tuple_without_database_call():
    client = FakeClient()

    assert _repository(client).insert_attraction_versions(()) == ()
    assert client.calls == []


def test_insert_attraction_versions_flattens_metadata_and_requests_authoritative_rows():
    record_a = _task7_version_record(
        attraction_id=TASK7_ATTRACTION_A, canonical_name="Caller A"
    )
    record_b = _task7_version_record(
        attraction_id=TASK7_ATTRACTION_B, canonical_name="Caller B"
    )
    client = FakeClient(
        [
            _task7_version_row(record_b, canonical_name="Database B"),
            _task7_version_row(record_a, canonical_name="Database A"),
        ]
    )

    result = _repository(client).insert_attraction_versions([record_a, record_b])

    call = client.calls[0]
    assert call["table"] == "rag_attraction_versions"
    assert call["operation"] == "insert"
    assert call["returning"] == ReturnMethod.representation
    assert isinstance(call["payload"], list)
    assert len(call["payload"]) == 2
    assert set(call["payload"][0]) == {
        "corpus_version_id",
        "attraction_id",
        "canonical_name",
        "aliases",
        "destination_code",
        "destination_level",
        "destination_name",
        "province_code",
        "province_name",
        "district_name",
        "category",
        "tags",
        "latitude",
        "longitude",
        "status",
        "metadata_hash",
    }
    assert call["payload"][0]["aliases"] == ["Alias one", "Alias two"]
    assert call["payload"][0]["tags"] == ["coast", "city"]
    assert call["payload"][0]["metadata_hash"] == "c" * 64
    assert "source_url" not in call["payload"][0]
    assert "source_label" not in call["payload"][0]
    assert [item.metadata.attraction_id for item in result] == [
        TASK7_ATTRACTION_A,
        TASK7_ATTRACTION_B,
    ]
    assert [item.metadata.canonical_name for item in result] == [
        "Database A",
        "Database B",
    ]


def test_duplicate_version_identity_is_rejected_before_database_call():
    record = _task7_version_record()
    client = FakeClient()

    with pytest.raises(AppError) as error:
        _repository(client).insert_attraction_versions([record, record])

    _assert_task7_error(
        error,
        "RAG_V2_VERSION_CONFLICT",
        "RAG V2 corpus version conflicts with existing data",
    )
    assert client.calls == []


@pytest.mark.parametrize(
    "code,expected",
    [
        ("23505", "RAG_V2_VERSION_CONFLICT"),
        ("23503", "RAG_V2_UNAVAILABLE"),
        ("23514", "RAG_V2_UNAVAILABLE"),
    ],
)
def test_version_insert_database_error_mapping(code, expected):
    client = FakeClient(APIError({"code": code, "message": "database failure"}))

    with pytest.raises(AppError) as error:
        _repository(client).insert_attraction_versions([_task7_version_record()])

    message = (
        "RAG V2 corpus version conflicts with existing data"
        if expected == "RAG_V2_VERSION_CONFLICT"
        else "RAG V2 persistence is unavailable"
    )
    _assert_task7_error(error, expected, message)


@pytest.mark.parametrize(
    "case",
    [
        "count-mismatch",
        "identity-mismatch",
        "malformed-row",
    ],
    ids=["count-mismatch", "identity-mismatch", "malformed-row"],
)
def test_version_insert_response_integrity_failures_are_unavailable(case):
    record_a = _task7_version_record(attraction_id=TASK7_ATTRACTION_A)
    record_b = _task7_version_record(attraction_id=TASK7_ATTRACTION_B)
    if case == "count-mismatch":
        response = [_task7_version_row(record_a)]
    elif case == "identity-mismatch":
        response = [
            _task7_version_row(record_a),
            _task7_version_row(
                _task7_version_record(attraction_id=TASK7_ATTRACTION_C)
            ),
        ]
    else:
        response = [{"corpus_version_id": str(TASK7_CORPUS_ID)}]
    client = FakeClient(response)

    with pytest.raises(AppError) as error:
        _repository(client).insert_attraction_versions([record_a, record_b])

    _assert_task7_error(error, "RAG_V2_UNAVAILABLE", "RAG V2 persistence is unavailable")


def test_empty_chunk_batch_returns_empty_tuple_without_database_call():
    client = FakeClient()

    assert _repository(client).insert_chunks(()) == ()
    assert client.calls == []


def test_insert_chunks_is_pending_only_and_preserves_content_hashes_and_provenance():
    record_a = _task7_chunk_insert(attraction_id=TASK7_ATTRACTION_A, chunk_key="chunk-a")
    record_b = _task7_chunk_insert(attraction_id=TASK7_ATTRACTION_B, chunk_key="chunk-b")
    client = FakeClient(
        [
            _task7_chunk_row(record_b.chunk),
            _task7_chunk_row(record_a.chunk),
        ]
    )

    result = _repository(client).insert_chunks([record_a, record_b])

    call = client.calls[0]
    assert call["table"] == "rag_attraction_chunks"
    assert call["operation"] == "insert"
    assert call["returning"] == ReturnMethod.representation
    assert isinstance(call["payload"], list)
    payload = call["payload"][0]
    assert set(payload) == {
        "corpus_version_id",
        "attraction_id",
        "chunk_key",
        "chunk_type",
        "ordinal",
        "content",
        "content_hash",
        "embedding_input_hash",
        "embedding_input_schema_version",
        "source_label",
        "source_url",
        "source_type",
        "reviewed_on",
        "embedding_model",
        "embedding_task",
        "embedding_dimensions",
        "embedding",
        "status",
        "embedding_error_code",
        "embedding_error_message",
    }
    assert payload["content"] == record_a.chunk.normalized_content
    assert payload["content_hash"] == record_a.chunk.content_hash
    assert payload["embedding_input_hash"] == record_a.chunk.embedding_input_hash
    assert payload["source_label"] == record_a.chunk.source_label
    assert payload["source_url"] == record_a.chunk.source_url
    assert payload["source_type"] == record_a.chunk.source_type
    assert payload["reviewed_on"] == record_a.chunk.reviewed_on.isoformat()
    assert payload["embedding_model"] == "jina-embeddings-v3"
    assert payload["embedding_task"] == "retrieval.passage"
    assert payload["embedding_dimensions"] == 1024
    assert payload["embedding_input_schema_version"] == "rag-v2-embedding-input-v1"
    assert payload["status"] == "pending"
    assert payload["embedding"] is None
    assert payload["embedding_error_code"] is None
    assert payload["embedding_error_message"] is None
    assert [item.chunk_key for item in result] == ["chunk-a", "chunk-b"]
    assert all(item.status is ChunkStatus.pending for item in result)
    assert all(item.embedding is None for item in result)


def test_duplicate_chunk_identity_is_rejected_before_database_call():
    record = _task7_chunk_insert()
    client = FakeClient()

    with pytest.raises(AppError) as error:
        _repository(client).insert_chunks([record, record])

    _assert_task7_error(
        error,
        "RAG_V2_VERSION_CONFLICT",
        "RAG V2 corpus version conflicts with existing data",
    )
    assert client.calls == []


@pytest.mark.parametrize(
    "code,expected",
    [
        ("23505", "RAG_V2_VERSION_CONFLICT"),
        ("23503", "RAG_V2_UNAVAILABLE"),
        ("23514", "RAG_V2_UNAVAILABLE"),
    ],
)
def test_chunk_insert_database_error_mapping(code, expected):
    client = FakeClient(APIError({"code": code, "message": "database failure"}))

    with pytest.raises(AppError) as error:
        _repository(client).insert_chunks([_task7_chunk_insert()])

    message = (
        "RAG V2 corpus version conflicts with existing data"
        if expected == "RAG_V2_VERSION_CONFLICT"
        else "RAG V2 persistence is unavailable"
    )
    _assert_task7_error(error, expected, message)


@pytest.mark.parametrize(
    "response",
    [
        [_task7_chunk_row(_task7_chunk())],
        [_task7_chunk_row(_task7_chunk()), _task7_chunk_row(_task7_chunk(chunk_key="chunk-c"))],
        [{"corpus_version_id": str(TASK7_CORPUS_ID)}],
    ],
    ids=["count-mismatch", "identity-mismatch", "malformed-row"],
)
def test_chunk_insert_response_integrity_failures_are_unavailable(response):
    record_a = _task7_chunk_insert(chunk_key="chunk-a")
    record_b = _task7_chunk_insert(chunk_key="chunk-b")
    client = FakeClient(response)

    with pytest.raises(AppError) as error:
        _repository(client).insert_chunks([record_a, record_b])

    _assert_task7_error(error, "RAG_V2_UNAVAILABLE", "RAG V2 persistence is unavailable")


def test_mark_chunk_embedded_uses_exact_identity_and_explicit_list_vector():
    chunk = _task7_chunk()
    client = FakeClient([_task7_chunk_row(chunk, embedding=TASK7_VECTOR, status="embedded")])

    result = _repository(client).mark_chunk_embedded(
        corpus_version_id=TASK7_CORPUS_ID,
        chunk_key=chunk.chunk_key,
        embedding=tuple(TASK7_VECTOR),
    )

    call = client.calls[0]
    assert call["table"] == "rag_attraction_chunks"
    assert call["operation"] == "update"
    assert call["filters"] == (
        ("corpus_version_id", str(TASK7_CORPUS_ID)),
        ("chunk_key", chunk.chunk_key),
    )
    assert call["payload"] == {
        "status": "embedded",
        "embedding": TASK7_VECTOR,
        "embedding_error_code": None,
        "embedding_error_message": None,
    }
    assert result.embedding == tuple(TASK7_VECTOR)
    assert isinstance(result.embedding, tuple)
    assert result.status is ChunkStatus.embedded


@pytest.mark.parametrize(
    "embedding",
    [
        [0.1] * 1023,
        [0.1] * 1025,
        [math.nan] + [0.1] * 1023,
        [math.inf] + [0.1] * 1023,
        [-math.inf] + [0.1] * 1023,
    ],
    ids=["short", "long", "nan", "positive-inf", "negative-inf"],
)
def test_mark_chunk_embedded_rejects_invalid_vectors_before_database_call(embedding):
    client = FakeClient()

    with pytest.raises(ValueError, match="^embedding must contain 1024 finite values$"):
        _repository(client).mark_chunk_embedded(
            corpus_version_id=TASK7_CORPUS_ID,
            chunk_key="chunk-a",
            embedding=embedding,
        )

    assert client.calls == []


@pytest.mark.parametrize(
    "embedding",
    [TASK7_VECTOR, TASK7_VECTOR_TEXT],
    ids=["json-array", "pgvector-text"],
)
def test_chunk_row_decodes_supported_vector_shapes_to_tuple(embedding):
    chunk = _task7_chunk()
    client = FakeClient([_task7_chunk_row(chunk, embedding=embedding, status="embedded")])

    result = _repository(client).mark_chunk_embedded(
        corpus_version_id=TASK7_CORPUS_ID,
        chunk_key=chunk.chunk_key,
        embedding=TASK7_VECTOR,
    )

    assert isinstance(result.embedding, tuple)
    assert len(result.embedding) == 1024
    assert result.embedding == tuple(TASK7_VECTOR)


@pytest.mark.parametrize(
    "embedding",
    [
        "[0.1,broken]",
        [0.1] * 1023,
        [math.nan] + [0.1] * 1023,
    ],
    ids=["malformed-text", "wrong-dimension", "non-finite"],
)
def test_invalid_returned_vector_maps_to_unavailable(embedding):
    chunk = _task7_chunk()
    client = FakeClient([_task7_chunk_row(chunk, embedding=embedding, status="embedded")])

    with pytest.raises(AppError) as error:
        _repository(client).mark_chunk_embedded(
            corpus_version_id=TASK7_CORPUS_ID,
            chunk_key=chunk.chunk_key,
            embedding=TASK7_VECTOR,
        )

    _assert_task7_error(error, "RAG_V2_UNAVAILABLE", "RAG V2 persistence is unavailable")


def test_mark_chunk_embedding_failed_strips_code_and_preserves_error_message():
    chunk = _task7_chunk()
    client = FakeClient(
        [
            _task7_chunk_row(
                chunk,
                status="failed",
                embedding_error_code="timeout",
                embedding_error_message="  temporary failure  ",
            )
        ]
    )

    result = _repository(client).mark_chunk_embedding_failed(
        corpus_version_id=TASK7_CORPUS_ID,
        chunk_key=chunk.chunk_key,
        error_code="  timeout  ",
        error_message="  temporary failure  ",
    )

    call = client.calls[0]
    assert call["filters"] == (
        ("corpus_version_id", str(TASK7_CORPUS_ID)),
        ("chunk_key", chunk.chunk_key),
    )
    assert call["payload"] == {
        "status": "failed",
        "embedding": None,
        "embedding_error_code": "timeout",
        "embedding_error_message": "  temporary failure  ",
    }
    assert result.embedding_error_code == "timeout"
    assert result.embedding_error_message == "  temporary failure  "


@pytest.mark.parametrize("error_code", ["", "   ", None, 123], ids=["empty", "spaces", "none", "non-string"])
def test_invalid_error_code_is_rejected_before_database_call(error_code):
    client = FakeClient()

    with pytest.raises(ValueError, match="^error_code must be a non-empty string$"):
        _repository(client).mark_chunk_embedding_failed(
            corpus_version_id=TASK7_CORPUS_ID,
            chunk_key="chunk-a",
            error_code=error_code,
            error_message=None,
        )

    assert client.calls == []


def test_reset_chunk_embedding_for_retry_sends_only_approved_reset_payload():
    chunk = _task7_chunk()
    client = FakeClient([_task7_chunk_row(chunk, status="pending")])

    result = _repository(client).reset_chunk_embedding_for_retry(
        corpus_version_id=TASK7_CORPUS_ID,
        chunk_key=chunk.chunk_key,
    )

    call = client.calls[0]
    assert call["filters"] == (
        ("corpus_version_id", str(TASK7_CORPUS_ID)),
        ("chunk_key", chunk.chunk_key),
    )
    assert call["payload"] == {
        "status": "pending",
        "embedding": None,
        "embedding_error_code": None,
        "embedding_error_message": None,
    }
    assert result.status is ChunkStatus.pending


@pytest.mark.parametrize(
    "method_name",
    [
        "mark_chunk_embedded",
        "mark_chunk_embedding_failed",
        "reset_chunk_embedding_for_retry",
    ],
)
def test_missing_chunk_mutation_maps_to_not_found(method_name):
    client = FakeClient([])
    repository = _repository(client)

    if method_name == "mark_chunk_embedded":
        call = lambda: getattr(repository, method_name)(
            corpus_version_id=TASK7_CORPUS_ID,
            chunk_key="chunk-a",
            embedding=TASK7_VECTOR,
        )
    elif method_name == "mark_chunk_embedding_failed":
        call = lambda: getattr(repository, method_name)(
            corpus_version_id=TASK7_CORPUS_ID,
            chunk_key="chunk-a",
            error_code="timeout",
        )
    else:
        call = lambda: getattr(repository, method_name)(
            corpus_version_id=TASK7_CORPUS_ID,
            chunk_key="chunk-a",
        )

    with pytest.raises(AppError) as error:
        call()

    _assert_task7_error(error, "RAG_V2_NOT_FOUND", "RAG V2 record not found")


@pytest.mark.parametrize(
    "prefix",
    [
        "RAG V2 chunk update requires status = 'staging'",
        "RAG V2 chunk embedding transition is invalid",
    ],
)
def test_approved_p0001_lifecycle_prefix_maps_to_invalid_lifecycle(prefix):
    client = FakeClient(APIError({"code": "P0001", "message": prefix + ": detail"}))

    with pytest.raises(AppError) as error:
        _repository(client).mark_chunk_embedding_failed(
            corpus_version_id=TASK7_CORPUS_ID,
            chunk_key="chunk-a",
            error_code="timeout",
        )

    _assert_task7_error(
        error,
        "RAG_V2_INVALID_LIFECYCLE",
        "RAG V2 lifecycle transition is invalid",
    )


def test_unrecognized_p0001_maps_to_unavailable():
    client = FakeClient(APIError({"code": "P0001", "message": "unrelated database error"}))

    with pytest.raises(AppError) as error:
        _repository(client).mark_chunk_embedding_failed(
            corpus_version_id=TASK7_CORPUS_ID,
            chunk_key="chunk-a",
            error_code="timeout",
        )

    _assert_task7_error(error, "RAG_V2_UNAVAILABLE", "RAG V2 persistence is unavailable")


def test_unexpected_task7_http_failure_is_unavailable_without_raw_text(caplog):
    client = FakeClient(httpx.ConnectError("raw provider response secret"))

    with caplog.at_level(logging.WARNING, logger="app.database"):
        with pytest.raises(AppError) as error:
            _repository(client).mark_chunk_embedding_failed(
                corpus_version_id=TASK7_CORPUS_ID,
                chunk_key="chunk-a",
                error_code="timeout",
            )

    _assert_task7_error(error, "RAG_V2_UNAVAILABLE", "RAG V2 persistence is unavailable")
    assert "raw provider response" not in str(error.value)
    assert "secret" not in caplog.text
