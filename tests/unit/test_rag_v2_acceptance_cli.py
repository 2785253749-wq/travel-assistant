from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import pytest

from app.rag_v2.acceptance import AcceptanceReport
from app.rag_v2.repository import CorpusVersion
from app.scripts.rag_v2_acceptance import (
    activate_dataset,
    build_parser,
    run_cli,
)


_CORPUS_ID = UUID("00000000-0000-4000-8000-000000000401")
_EXPECTED_ACTIVE_ID = UUID("00000000-0000-4000-8000-000000000402")
_MANIFEST_HASH = "a" * 64


@dataclass
class _FakeRepository:
    corpus: object

    def __post_init__(self) -> None:
        self.activation_calls: list[dict[str, object]] = []
        self.read_calls: list[dict[str, object]] = []

    def get_corpus_version(self, **kwargs: object) -> object:
        self.read_calls.append(kwargs)
        return self.corpus

    def activate_corpus(self, **kwargs: object) -> None:
        self.activation_calls.append(kwargs)


def _corpus(
    *,
    dataset_key: str = "rag-v2-production",
    status: str = "staging",
    ready_for_activation: bool = True,
    manifest_hash: str = _MANIFEST_HASH,
) -> SimpleNamespace:
    return SimpleNamespace(
        corpus_version_id=_CORPUS_ID,
        dataset_key=dataset_key,
        version_label="production-v1",
        manifest_hash=manifest_hash,
        status=status,
        ready_for_activation=ready_for_activation,
        created_at=datetime.now(timezone.utc),
        activated_at=None,
        superseded_at=None,
    )


def _context(repository: _FakeRepository) -> SimpleNamespace:
    return SimpleNamespace(repository=repository)


def _activate(
    repository: _FakeRepository,
    *,
    role: str = "production",
    dataset_key: str = "rag-v2-production",
    confirmation: str = "ACTIVATE",
    expected_active_corpus_version_id: UUID | None = _EXPECTED_ACTIVE_ID,
) -> AcceptanceReport:
    return activate_dataset(
        context=_context(repository),
        role=role,
        dataset_key=dataset_key,
        corpus_version_id=_CORPUS_ID,
        manifest_hash=_MANIFEST_HASH,
        confirmation=confirmation,
        expected_active_corpus_version_id=expected_active_corpus_version_id,
    )


def test_parser_exposes_exact_subcommands() -> None:
    parser = build_parser()
    subparsers = next(
        action
        for action in parser._actions
        if isinstance(action, argparse._SubParsersAction)
    )

    assert tuple(subparsers.choices) == (
        "preflight",
        "smoke",
        "acceptance-import",
        "incremental-check",
        "production-import",
        "retrieval-check",
        "activation-preview",
        "activate",
        "post-activation-smoke",
    )


def test_cli_dispatches_preflight_without_activation(monkeypatch: pytest.MonkeyPatch) -> None:
    repository = _FakeRepository(_corpus(status="active"))
    context = _context(repository)
    reports: list[object] = []

    monkeypatch.setattr(
        "app.scripts.rag_v2_acceptance._build_context",
        lambda _args: context,
        raising=False,
    )
    monkeypatch.setattr(
        "app.scripts.rag_v2_acceptance.run_preflight",
        lambda *, context: SimpleNamespace(failures=()),
        raising=False,
    )
    monkeypatch.setattr(
        "app.scripts.rag_v2_acceptance.write_report",
        lambda **kwargs: reports.append(kwargs["report"]),
        raising=False,
    )

    assert run_cli(["preflight"]) == 0
    assert repository.activation_calls == []
    assert reports


def test_cli_dispatches_smoke_without_hidden_activation(monkeypatch: pytest.MonkeyPatch) -> None:
    repository = _FakeRepository(_corpus(status="active"))
    context = _context(repository)
    reports: list[object] = []

    monkeypatch.setattr(
        "app.scripts.rag_v2_acceptance._build_context",
        lambda _args: context,
        raising=False,
    )
    monkeypatch.setattr(
        "app.scripts.rag_v2_acceptance.run_import",
        lambda **_: SimpleNamespace(ready_for_activation=True),
        raising=False,
    )
    monkeypatch.setattr(
        "app.scripts.rag_v2_acceptance.write_report",
        lambda **kwargs: reports.append(kwargs["report"]),
        raising=False,
    )

    assert run_cli(["smoke"]) == 0
    assert repository.activation_calls == []
    assert reports


def test_cli_dispatches_retrieval_check_without_activation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = _FakeRepository(_corpus(status="active"))
    context = _context(repository)
    reports: list[object] = []

    monkeypatch.setattr(
        "app.scripts.rag_v2_acceptance._build_context",
        lambda _args: context,
        raising=False,
    )
    monkeypatch.setattr(
        "app.scripts.rag_v2_acceptance.run_retrieval_check",
        lambda **_: SimpleNamespace(status="passed"),
        raising=False,
    )
    monkeypatch.setattr(
        "app.scripts.rag_v2_acceptance.write_report",
        lambda **kwargs: reports.append(kwargs["report"]),
        raising=False,
    )

    assert run_cli(["retrieval-check"]) == 0
    assert repository.activation_calls == []
    assert reports


def test_preview_reports_retrieval_not_yet_run(monkeypatch: pytest.MonkeyPatch) -> None:
    repository = _FakeRepository(_corpus(status="staging"))
    context = _context(repository)
    reports: list[object] = []

    monkeypatch.setattr(
        "app.scripts.rag_v2_acceptance._build_context",
        lambda _args: context,
        raising=False,
    )
    monkeypatch.setattr(
        "app.scripts.rag_v2_acceptance.write_report",
        lambda **kwargs: reports.append(kwargs["report"]),
        raising=False,
    )

    assert run_cli(["activation-preview"]) == 0
    assert repository.activation_calls == []
    assert reports
    assert "NOT YET RUN" in str(reports[0].details)


@pytest.mark.parametrize(
    ("command", "wrong_dataset_key"),
    (
        ("smoke", "rag-v2-production"),
        ("acceptance-import", "rag-v2-smoke"),
        ("production-import", "rag-v2-acceptance"),
    ),
)
def test_import_commands_enforce_dataset_isolation(
    command: str,
    wrong_dataset_key: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = _FakeRepository(_corpus(status="active"))
    context = _context(repository)
    import_calls: list[object] = []

    monkeypatch.setattr(
        "app.scripts.rag_v2_acceptance._build_context",
        lambda _args: context,
        raising=False,
    )
    monkeypatch.setattr(
        "app.scripts.rag_v2_acceptance.run_import",
        lambda **kwargs: import_calls.append(kwargs)
        or SimpleNamespace(ready_for_activation=True),
        raising=False,
    )

    assert run_cli([command, "--dataset-key", wrong_dataset_key]) == 1
    assert import_calls == []


def test_activation_preview_reports_authoritative_counts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = _FakeRepository(_corpus(status="staging"))
    rows = (
        SimpleNamespace(
            attraction_id=UUID("00000000-0000-4000-8000-000000000411"),
            status="embedded",
            embedding=(0.1,),
            embedding_error_code=None,
        ),
        SimpleNamespace(
            attraction_id=UUID("00000000-0000-4000-8000-000000000411"),
            status="pending",
            embedding=None,
            embedding_error_code=None,
        ),
        SimpleNamespace(
            attraction_id=UUID("00000000-0000-4000-8000-000000000412"),
            status="failed",
            embedding=None,
            embedding_error_code="provider_error",
        ),
    )
    repository.list_chunk_rows = lambda **_: rows  # type: ignore[attr-defined]
    context = _context(repository)
    reports: list[object] = []

    monkeypatch.setattr(
        "app.scripts.rag_v2_acceptance._build_context",
        lambda _args: context,
        raising=False,
    )
    monkeypatch.setattr(
        "app.scripts.rag_v2_acceptance.write_report",
        lambda **kwargs: reports.append(kwargs["report"]),
        raising=False,
    )

    assert run_cli(
        [
            "activation-preview",
            "--corpus-version-id",
            str(_CORPUS_ID),
            "--current-active-corpus-version-id",
            str(_EXPECTED_ACTIVE_ID),
        ]
    ) == 0

    assert reports
    details = reports[0].details
    assert details["attraction_count"] == 2
    assert details["chunk_count"] == 3
    assert details["embedded_count"] == 1
    assert details["pending_count"] == 1
    assert details["failed_count"] == 1
    assert details["ready_for_activation"] is True
    assert details["current_active_corpus_version_id"] == _EXPECTED_ACTIVE_ID
    assert details["retrieval acceptance"] == "NOT YET RUN"
    assert repository.activation_calls == []


def test_cli_writes_safe_failure_report_for_unexpected_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = _FakeRepository(_corpus(status="active"))
    context = _context(repository)
    reports: list[object] = []

    monkeypatch.setattr(
        "app.scripts.rag_v2_acceptance._build_context",
        lambda _args: context,
        raising=False,
    )

    def raise_unexpected_failure(**_: object) -> object:
        raise RuntimeError("secret provider response")

    monkeypatch.setattr(
        "app.scripts.rag_v2_acceptance.run_preflight",
        raise_unexpected_failure,
        raising=False,
    )
    monkeypatch.setattr(
        "app.scripts.rag_v2_acceptance.write_report",
        lambda **kwargs: reports.append(kwargs["report"]),
        raising=False,
    )

    assert run_cli(["preflight"]) == 1
    assert reports
    assert reports[0].status == "failed"
    assert "secret provider response" not in str(reports[0].details)


@pytest.mark.parametrize(
    "confirmation",
    ("activate", "Activate", "YES", "yes", "y", "", "   "),
)
def test_activate_rejects_confirmation_other_than_activate(
    confirmation: str,
) -> None:
    repository = _FakeRepository(_corpus())

    with pytest.raises(Exception):
        _activate(repository, confirmation=confirmation)

    assert repository.activation_calls == []


def test_production_activate_requires_fresh_authoritative_readiness() -> None:
    repository = _FakeRepository(
        _corpus(status="staging", ready_for_activation=False)
    )

    with pytest.raises(Exception):
        _activate(repository)

    assert repository.read_calls
    assert repository.activation_calls == []


def test_activate_forwards_expected_active_corpus_id() -> None:
    repository = _FakeRepository(_corpus())

    report = _activate(repository)

    assert report.status == "passed"
    assert repository.activation_calls == [
        {
            "dataset_key": "rag-v2-production",
            "corpus_version_id": _CORPUS_ID,
            "expected_active_corpus_version_id": _EXPECTED_ACTIVE_ID,
        }
    ]


def test_acceptance_and_smoke_activation_are_dataset_isolated() -> None:
    for role, dataset_key in (
        ("smoke", "rag-v2-smoke"),
        ("acceptance", "rag-v2-acceptance"),
    ):
        repository = _FakeRepository(
            _corpus(dataset_key=dataset_key)
        )

        _activate(
            repository,
            role=role,
            dataset_key=dataset_key,
            expected_active_corpus_version_id=None,
        )

        assert repository.activation_calls[0]["dataset_key"] == dataset_key
        assert dataset_key != "rag-v2-production"


def test_only_activate_dispatch_path_calls_activate_corpus() -> None:
    repository = _FakeRepository(_corpus())

    _activate(repository)

    assert len(repository.activation_calls) == 1


def test_cli_returns_nonzero_for_failed_required_cases(monkeypatch: pytest.MonkeyPatch) -> None:
    repository = _FakeRepository(_corpus(status="active"))
    context = _context(repository)
    reports: list[object] = []

    monkeypatch.setattr(
        "app.scripts.rag_v2_acceptance._build_context",
        lambda _args: context,
        raising=False,
    )
    monkeypatch.setattr(
        "app.scripts.rag_v2_acceptance.run_retrieval_check",
        lambda **_: SimpleNamespace(status="failed"),
        raising=False,
    )
    monkeypatch.setattr(
        "app.scripts.rag_v2_acceptance.write_report",
        lambda **kwargs: reports.append(kwargs["report"]),
        raising=False,
    )

    assert run_cli(["retrieval-check"]) == 1
    assert repository.activation_calls == []
    assert reports
