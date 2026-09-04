from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, is_dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal
from uuid import UUID

from pydantic import SecretStr

from app.core.config import Settings

if TYPE_CHECKING:
    from app.rag_v2.importer import RagV2Importer
    from app.rag_v2.passage_embedding import PassageEmbedder
    from app.rag_v2.repository import RagV2Repository
    from app.rag_v2.retrieval import QueryEmbedder, RetrievalService


DatasetRole = Literal["smoke", "acceptance", "production"]

DATASET_KEYS: Mapping[DatasetRole, str] = {
    "smoke": "rag-v2-smoke",
    "acceptance": "rag-v2-acceptance",
    "production": "rag-v2-production",
}

_PREFLIGHT_CORPUS_ID = UUID("00000000-0000-4000-8000-000000000299")
_REPORT_FILENAME = re.compile(r"[^A-Za-z0-9_.-]+")


@dataclass(frozen=True)
class AcceptanceContext:
    settings: Settings
    repository: RagV2Repository
    importer: RagV2Importer
    passage_embedder: PassageEmbedder
    query_embedder: QueryEmbedder
    retrieval_service: RetrievalService
    report_dir: Path


@dataclass(frozen=True)
class CredentialState:
    name: str
    state: Literal["SET", "MISSING"]


@dataclass(frozen=True)
class PreflightResult:
    credentials: tuple[CredentialState, ...]
    passage_embedding_available: bool
    query_embedding_available: bool
    supabase_available: bool
    candidate_probe_available: bool
    failures: tuple[str, ...]


@dataclass(frozen=True)
class AcceptanceReport:
    run_id: str
    command: str
    status: Literal["passed", "failed"]
    dataset_key: str | None
    version_label: str | None
    corpus_version_id: UUID | None
    manifest_hash: str | None
    details: Mapping[str, object]


def validate_dataset_key(*, role: DatasetRole, dataset_key: str) -> None:
    expected = DATASET_KEYS.get(role)
    if expected is None or dataset_key != expected:
        raise ValueError("dataset key does not match role")


def credential_states(settings: Settings) -> tuple[CredentialState, ...]:
    values = (
        ("JINA_API_KEY", settings.jina_api_key),
        ("SUPABASE_URL", settings.supabase_url),
        ("SUPABASE_SERVICE_KEY", settings.supabase_service_key),
    )
    return tuple(
        CredentialState(name=name, state="SET" if _is_set(value) else "MISSING")
        for name, value in values
    )


def run_preflight(
    *,
    context: AcceptanceContext,
    probe_dataset_key: str = "rag-v2-preflight",
) -> PreflightResult:
    credentials = credential_states(context.settings)
    failures = [
        f"missing credential: {credential.name}"
        for credential in credentials
        if credential.state == "MISSING"
    ]

    passage_vector = None
    try:
        passage_vector = context.passage_embedder.embed_passage(
            "RAG V2 preflight passage probe"
        )
        _require_probe_vector(passage_vector)
        passage_available = True
    except Exception:
        passage_available = False
        failures.append("passage embedding prerequisite unavailable")

    query_vector = None
    try:
        query_vector = context.query_embedder.embed_query("RAG V2 preflight query probe")
        _require_probe_vector(query_vector)
        query_available = True
    except Exception:
        query_available = False
        failures.append("query embedding prerequisite unavailable")

    try:
        context.repository.get_corpus_version(
            corpus_version_id=_PREFLIGHT_CORPUS_ID,
        )
        supabase_available = True
    except Exception:
        supabase_available = False
        failures.append("Supabase prerequisite unavailable")

    candidate_available = False
    if query_vector is not None:
        try:
            context.repository.match_chunks(
                dataset_key=probe_dataset_key,
                query_embedding=query_vector,
                candidate_k=1,
            )
            candidate_available = True
        except Exception:
            failures.append("candidate retrieval prerequisite unavailable")
    else:
        failures.append("candidate retrieval prerequisite unavailable")

    return PreflightResult(
        credentials=credentials,
        passage_embedding_available=passage_available,
        query_embedding_available=query_available,
        supabase_available=supabase_available,
        candidate_probe_available=candidate_available,
        failures=tuple(failures),
    )


def write_report(*, report: AcceptanceReport, report_dir: Path) -> Path:
    report_dir.mkdir(parents=True, exist_ok=True)
    safe_run_id = _REPORT_FILENAME.sub("_", report.run_id).strip("._") or "report"
    path = report_dir / f"{safe_run_id}.json"
    payload = {
        "run_id": report.run_id,
        "command": report.command,
        "status": report.status,
        "dataset_key": report.dataset_key,
        "version_label": report.version_label,
        "corpus_version_id": (
            str(report.corpus_version_id)
            if report.corpus_version_id is not None
            else None
        ),
        "manifest_hash": report.manifest_hash,
        "details": _json_safe(report.details),
    }
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path


def _is_set(value: object) -> bool:
    if value is None:
        return False
    if isinstance(value, SecretStr):
        return bool(value.get_secret_value().strip())
    return bool(str(value).strip())


def _require_probe_vector(vector: object) -> None:
    if not isinstance(vector, tuple) or len(vector) != 1024:
        raise ValueError("preflight vector is invalid")


def _json_safe(value: object) -> object:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if is_dataclass(value):
        return _json_safe(value.__dict__)
    raise TypeError("report details must be JSON-serializable")
