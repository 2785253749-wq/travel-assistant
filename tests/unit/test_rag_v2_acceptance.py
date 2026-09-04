from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import pytest
from pydantic import SecretStr

from app.core.config import Settings
from app.rag_v2.acceptance import (
    DATASET_KEYS,
    AcceptanceContext,
    AcceptanceReport,
    CredentialState,
    DatasetRole,
    PreflightResult,
    credential_states,
    run_preflight,
    validate_dataset_key,
    write_report,
)


_CORPUS_ID = UUID("00000000-0000-4000-8000-000000000201")
_TEST_JINA_SECRET = "unit-only-jina-secret"
_TEST_SUPABASE_SECRET = "unit-only-supabase-secret"


class _FakePassageEmbedder:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def embed_passage(self, text: str) -> tuple[float, ...]:
        self.calls.append(text)
        return (0.0,) * 1024


class _FakeQueryEmbedder:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def embed_query(self, query: str) -> tuple[float, ...]:
        self.calls.append(query)
        return (0.0,) * 1024


class _FakeRepository:
    def __init__(self, *, read_failure: Exception | None = None) -> None:
        self.read_failure = read_failure
        self.read_calls: list[UUID] = []
        self.match_calls: list[dict[str, object]] = []
        self.activation_calls = 0

    def get_corpus_version(self, *, corpus_version_id: UUID) -> object | None:
        self.read_calls.append(corpus_version_id)
        if self.read_failure is not None:
            raise self.read_failure
        return None

    def match_chunks(self, **kwargs: object) -> tuple[object, ...]:
        self.match_calls.append(kwargs)
        return ()

    def activate_corpus(self, **_: object) -> None:
        self.activation_calls += 1
        raise AssertionError("preflight must not activate a corpus")


def _settings() -> Settings:
    return Settings(
        _env_file=None,
        jina_api_key=SecretStr(_TEST_JINA_SECRET),
        supabase_url="https://project.supabase.co",
        supabase_service_key=SecretStr(_TEST_SUPABASE_SECRET),
    )


def _context(tmp_path: Path, repository: _FakeRepository | None = None) -> AcceptanceContext:
    return AcceptanceContext(
        settings=_settings(),
        repository=repository or _FakeRepository(),
        importer=SimpleNamespace(),
        passage_embedder=_FakePassageEmbedder(),
        query_embedder=_FakeQueryEmbedder(),
        retrieval_service=SimpleNamespace(),
        report_dir=tmp_path / ".rag-v2-reports",
    )


def _report(**details: object) -> AcceptanceReport:
    return AcceptanceReport(
        run_id="run-001",
        command="preflight",
        status="failed",
        dataset_key="rag-v2-acceptance",
        version_label="v1",
        corpus_version_id=_CORPUS_ID,
        manifest_hash="a" * 64,
        details=details,
    )


def test_dataset_roles_use_exact_keys() -> None:
    roles: tuple[DatasetRole, ...] = ("smoke", "acceptance", "production")

    assert tuple(DATASET_KEYS) == roles
    assert DATASET_KEYS == {
        "smoke": "rag-v2-smoke",
        "acceptance": "rag-v2-acceptance",
        "production": "rag-v2-production",
    }


def test_dataset_role_rejects_cross_role_key() -> None:
    with pytest.raises(ValueError):
        validate_dataset_key(role="production", dataset_key="rag-v2-acceptance")


def test_credential_states_never_include_secret_values() -> None:
    states = credential_states(_settings())
    serialized = json.dumps(
        [{"name": state.name, "state": state.state} for state in states],
        sort_keys=True,
    )

    assert isinstance(states, tuple)
    assert all(isinstance(state, CredentialState) for state in states)
    assert [(state.name, state.state) for state in states] == [
        ("JINA_API_KEY", "SET"),
        ("SUPABASE_URL", "SET"),
        ("SUPABASE_SERVICE_KEY", "SET"),
    ]
    assert _TEST_JINA_SECRET not in serialized
    assert _TEST_SUPABASE_SECRET not in serialized


def test_report_serialization_is_secret_safe(tmp_path: Path) -> None:
    report = _report(
        credential_states=(
            {"name": "JINA_API_KEY", "state": "SET"},
            {"name": "SUPABASE_SERVICE_KEY", "state": "SET"},
        )
    )

    path = write_report(report=report, report_dir=tmp_path / ".rag-v2-reports")
    serialized = path.read_text(encoding="utf-8")

    assert "JINA_API_KEY" in serialized
    assert '"state": "SET"' in serialized
    assert _TEST_JINA_SECRET not in serialized
    assert _TEST_SUPABASE_SECRET not in serialized


def test_preflight_uses_non_production_probe_key(tmp_path: Path) -> None:
    repository = _FakeRepository()

    result = run_preflight(context=_context(tmp_path, repository))

    assert isinstance(result, PreflightResult)
    assert repository.match_calls
    assert repository.match_calls[0]["dataset_key"] == "rag-v2-preflight"
    assert repository.match_calls[0]["dataset_key"] not in DATASET_KEYS.values()


def test_preflight_does_not_call_activation(tmp_path: Path) -> None:
    repository = _FakeRepository()

    run_preflight(context=_context(tmp_path, repository))

    assert repository.activation_calls == 0


def test_preflight_reports_missing_prerequisite_without_ddl(tmp_path: Path) -> None:
    repository = _FakeRepository(
        read_failure=RuntimeError("raw database prerequisite secret")
    )

    result = run_preflight(context=_context(tmp_path, repository))

    assert isinstance(result, PreflightResult)
    assert result.failures
    assert all("raw database prerequisite secret" not in failure for failure in result.failures)
    assert repository.activation_calls == 0


def test_report_path_is_local_and_ignored(tmp_path: Path) -> None:
    report_dir = tmp_path / ".rag-v2-reports"
    path = write_report(report=_report(), report_dir=report_dir)
    project_root = Path(__file__).resolve().parents[2]
    gitignore = (project_root / ".gitignore").read_text(encoding="utf-8")

    assert path.parent == report_dir
    assert path.is_relative_to(report_dir)
    assert ".rag-v2-reports/" in gitignore


def test_acceptance_report_round_trips_json(tmp_path: Path) -> None:
    report = AcceptanceReport(
        run_id="run-round-trip",
        command="activation-preview",
        status="passed",
        dataset_key="rag-v2-production",
        version_label="2026-09-04",
        corpus_version_id=_CORPUS_ID,
        manifest_hash="b" * 64,
        details={"ready_for_activation": True, "required_cases_passed": 10},
    )

    payload = json.loads(
        write_report(report=report, report_dir=tmp_path / ".rag-v2-reports").read_text(
            encoding="utf-8"
        )
    )

    assert payload["run_id"] == "run-round-trip"
    assert payload["command"] == "activation-preview"
    assert payload["status"] == "passed"
    assert payload["dataset_key"] == "rag-v2-production"
    assert payload["version_label"] == "2026-09-04"
    assert payload["corpus_version_id"] == str(_CORPUS_ID)
    assert payload["manifest_hash"] == "b" * 64
    assert payload["details"] == {
        "ready_for_activation": True,
        "required_cases_passed": 10,
    }


def test_failed_report_contains_no_raw_upstream_text(tmp_path: Path) -> None:
    report = _report(
        failure_code="RAG_V2_UNAVAILABLE",
        failure_message="Supabase prerequisite unavailable",
    )

    serialized = write_report(
        report=report,
        report_dir=tmp_path / ".rag-v2-reports",
    ).read_text(encoding="utf-8")

    assert "RAG_V2_UNAVAILABLE" in serialized
    assert "raw database response body" not in serialized
    assert "raw provider secret" not in serialized
