from dataclasses import FrozenInstanceError, fields, is_dataclass
from datetime import UTC, datetime
from importlib import import_module
import logging
import sys
from types import SimpleNamespace
from typing import get_type_hints
from uuid import UUID

import httpx
import pytest
from postgrest.exceptions import APIError

from app.core.config import Settings
from app.core.errors import AppError
from app.rag_v2.models import StableAttraction


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
        self.operation = "select"
        self.selected = columns
        return self

    def eq(self, column, value):
        self.filters.append((column, value))
        return self

    def insert(self, payload):
        self.operation = "insert"
        self.payload = payload
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
