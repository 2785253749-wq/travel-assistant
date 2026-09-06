from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, is_dataclass, replace
from datetime import date, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Literal
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import SecretStr
import yaml

from app.core.config import Settings
from app.rag_v2.acceptance_cases import (
    AcceptanceCase,
    AcceptanceObservation,
    case_file_sha256,
    evaluate_acceptance_case,
    required_cases_pass,
)
from app.rag_v2.authoring import AuthoringDocument
from app.rag_v2.chunking import SemanticChunker
from app.rag_v2.hashing import manifest_hash, metadata_hash
from app.rag_v2.importer import (
    CorpusImportInput,
    CorpusImportResult,
    ImportAttraction,
)
from app.rag_v2.models import (
    EmbeddingTask,
    EmbeddingProfile,
    ManifestAttraction,
    ManifestChunk,
    ManifestInput,
)

if TYPE_CHECKING:
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
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if is_dataclass(value):
        return _json_safe(value.__dict__)
    raise TypeError("report details must be JSON-serializable")


def build_import_request(
    *,
    documents: Sequence[AuthoringDocument],
    dataset_key: str,
    version_label: str,
    corpus_version_id: UUID,
    chunker: SemanticChunker,
) -> CorpusImportInput:
    attractions: list[ImportAttraction] = []
    manifest_attractions: list[ManifestAttraction] = []

    for document in documents:
        for authored in document.attractions:
            chunks = tuple(
                chunk
                for section in authored.sections
                for chunk in chunker.chunk(
                    section,
                    attraction=authored.metadata,
                )
            )
            attractions.append(
                ImportAttraction(
                    registry_key=authored.registry_key,
                    metadata=authored.metadata,
                    sections=authored.sections,
                )
            )
            manifest_attractions.append(
                ManifestAttraction(
                    attraction_id=authored.metadata.attraction_id,
                    metadata_hash=metadata_hash(authored.metadata),
                    chunks=tuple(
                        ManifestChunk(
                            chunk_key=chunk.chunk_key,
                            chunk_type=chunk.chunk_type,
                            ordinal=chunk.ordinal,
                            content_hash=chunk.content_hash,
                            embedding_input_hash=chunk.embedding_input_hash,
                            source_label=chunk.source_label,
                            source_url=chunk.source_url,
                            source_type=chunk.source_type,
                            reviewed_on=chunk.reviewed_on,
                        )
                        for chunk in chunks
                    ),
                )
            )

    manifest = ManifestInput(
        schema_version="rag-v2-manifest-v1",
        dataset_key=dataset_key,
        embedding_profile=EmbeddingProfile(
            model="jina-embeddings-v3",
            task=EmbeddingTask.passage,
            dimensions=1024,
            input_schema_version="rag-v2-embedding-input-v1",
        ),
        attractions=tuple(manifest_attractions),
    )
    manifest_hash(manifest)
    return CorpusImportInput(
        corpus_version_id=corpus_version_id,
        dataset_key=dataset_key,
        version_label=version_label,
        manifest=manifest,
        attractions=tuple(attractions),
    )


def run_import(
    *,
    context: AcceptanceContext,
    request: CorpusImportInput,
) -> CorpusImportResult:
    return context.importer.import_corpus(request)


def run_same_manifest_check(
    *,
    context: AcceptanceContext,
    request: CorpusImportInput,
) -> AcceptanceReport:
    first = run_import(context=context, request=request)
    second = run_import(context=context, request=request)
    return _import_report(
        command="same-manifest-check",
        result=second,
        details={
            "first_manifest_hash": manifest_hash(request.manifest),
            "second_manifest_hash": manifest_hash(request.manifest),
            "first_result": first,
            "second_result": second,
        },
    )


def run_incremental_check(
    *,
    context: AcceptanceContext,
    baseline_documents: Sequence[AuthoringDocument],
    patch_path: Path,
    baseline_corpus_version_id: UUID,
    baseline_version_label: str,
    next_version_label: str,
) -> AcceptanceReport:
    baseline = context.repository.get_corpus_version(
        corpus_version_id=baseline_corpus_version_id
    )
    if (
        baseline is None
        or baseline.status not in {"active", "superseded"}
        or baseline.dataset_key != DATASET_KEYS["acceptance"]
    ):
        return _failure_report(
            command="incremental-check",
            dataset_key=getattr(baseline, "dataset_key", None),
            version_label=baseline_version_label,
            corpus_version_id=baseline_corpus_version_id,
            reason="historical acceptance baseline is not active or superseded",
        )

    patched_documents = _apply_incremental_patch(
        documents=tuple(baseline_documents),
        patch_path=patch_path,
    )
    next_corpus_version_id = uuid5(
        NAMESPACE_URL,
        f"rag-v2:{baseline.dataset_key}:{next_version_label}",
    )
    request = build_import_request(
        documents=patched_documents,
        dataset_key=baseline.dataset_key,
        version_label=next_version_label,
        corpus_version_id=next_corpus_version_id,
        chunker=SemanticChunker(),
    )
    result = run_import(context=context, request=request)
    return _import_report(
        command="incremental-check",
        result=result,
        details={
            "baseline_corpus_version_id": baseline_corpus_version_id,
            "baseline_version_label": baseline_version_label,
            "reused_chunk_keys": result.reused_chunk_keys,
            "embedded_chunk_keys": result.embedded_chunk_keys,
            "ready_for_activation": result.ready_for_activation,
        },
    )


def run_retrieval_check(
    *,
    context: AcceptanceContext,
    cases: Sequence[AcceptanceCase],
    dataset_key: str,
    required_active_corpus_id: UUID,
    case_set_id: str | None = None,
    case_file_path: Path | None = None,
) -> AcceptanceReport:
    corpus = context.repository.get_corpus_version(
        corpus_version_id=required_active_corpus_id
    )
    if (
        corpus is None
        or corpus.corpus_version_id != required_active_corpus_id
        or corpus.dataset_key != dataset_key
        or corpus.status != "active"
    ):
        return _failure_report(
            command="retrieval-check",
            dataset_key=dataset_key,
            corpus_version_id=required_active_corpus_id,
            reason="required corpus is not active",
        )

    observations = _retrieve_observations(
        context=context,
        cases=tuple(cases),
        dataset_key=dataset_key,
    )
    passed = required_cases_pass(observations)
    retrieval_details: dict[str, object] = {
        "observations": observations,
        "required_cases_passed": sum(
            observation.evaluation.passed
            for observation in observations
            if observation.case.required
        ),
        "required_cases_failed": sum(
            not observation.evaluation.passed
            for observation in observations
            if observation.case.required
        ),
    }
    if case_set_id is not None and case_file_path is not None:
        retrieval_details.update(
            {
                "case_set_id": case_set_id,
                "case_file_sha256": case_file_sha256(case_file_path),
            }
        )
    return _report(
        command="retrieval-check",
        status="passed" if passed else "failed",
        dataset_key=dataset_key,
        corpus=corpus,
        details=retrieval_details,
    )


def run_post_activation_smoke(
    *,
    context: AcceptanceContext,
    cases: Sequence[AcceptanceCase],
    dataset_key: str,
) -> AcceptanceReport:
    known_corpus = getattr(context.repository, "corpus", None)
    if known_corpus is not None and known_corpus.status != "active":
        return _failure_report(
            command="post-activation-smoke",
            dataset_key=dataset_key,
            corpus_version_id=getattr(known_corpus, "corpus_version_id", None),
            reason="active corpus is required for smoke retrieval",
        )

    try:
        observations = _retrieve_observations(
            context=context,
            cases=tuple(cases),
            dataset_key=dataset_key,
        )
    except Exception:
        return _failure_report(
            command="post-activation-smoke",
            dataset_key=dataset_key,
            corpus=known_corpus,
            previous_corpus_version_id=getattr(
                context.repository, "previous_corpus_id", None
            ),
            reason="post-activation retrieval is unavailable",
        )

    passed = required_cases_pass(observations)
    return _report(
        command="post-activation-smoke",
        status="passed" if passed else "failed",
        dataset_key=dataset_key,
        corpus=known_corpus,
        details={
            "observations": observations,
            "current_corpus_version_id": getattr(
                known_corpus, "corpus_version_id", None
            ),
            "previous_corpus_version_id": getattr(
                context.repository, "previous_corpus_id", None
            ),
        },
    )


def _retrieve_observations(
    *,
    context: AcceptanceContext,
    cases: tuple[AcceptanceCase, ...],
    dataset_key: str,
) -> tuple[AcceptanceObservation, ...]:
    observations: list[AcceptanceObservation] = []
    for case in cases:
        result = context.retrieval_service.retrieve(
            query=case.query,
            dataset_key=dataset_key,
            destination_code=case.expected_destination_code,
        )
        observations.append(
            evaluate_acceptance_case(case=case, result=result)
        )
    return tuple(observations)


def _apply_incremental_patch(
    *,
    documents: tuple[AuthoringDocument, ...],
    patch_path: Path,
) -> tuple[AuthoringDocument, ...]:
    payload = yaml.safe_load(patch_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("incremental patch must be a mapping")
    target = payload.get("target")
    if not isinstance(target, dict):
        raise ValueError("incremental patch target must be a mapping")

    destination_code = target.get("destination_code")
    registry_key = target.get("registry_key")
    section_name = target.get("section")
    replacement_content = payload.get("replacement_content")
    if not all(
        isinstance(value, str) and value.strip()
        for value in (
            destination_code,
            registry_key,
            section_name,
            replacement_content,
        )
    ):
        raise ValueError("incremental patch fields are invalid")

    updated_documents: list[AuthoringDocument] = []
    matched = False
    for document in documents:
        updated_attractions = []
        for attraction in document.attractions:
            if (
                document.destination.destination_code == destination_code
                and attraction.registry_key == registry_key
            ):
                sections = []
                for section in attraction.sections:
                    if section.chunk_type.value == section_name:
                        matched = True
                        sections.append(
                            section.model_copy(
                                update={"content": replacement_content}
                            )
                        )
                    else:
                        sections.append(section)
                attraction = replace(attraction, sections=tuple(sections))
            updated_attractions.append(attraction)
        updated_documents.append(
            replace(document, attractions=tuple(updated_attractions))
        )
    if not matched:
        raise ValueError("incremental patch target was not found")
    return tuple(updated_documents)


def _import_report(
    *,
    command: str,
    result: CorpusImportResult,
    details: Mapping[str, object],
) -> AcceptanceReport:
    return _report(
        command=command,
        status="passed" if result.ready_for_activation else "failed",
        dataset_key=result.corpus.dataset_key,
        corpus=result.corpus,
        details=details,
    )


def _report(
    *,
    command: str,
    status: Literal["passed", "failed"],
    dataset_key: str | None,
    corpus: object | None = None,
    details: Mapping[str, object] | None = None,
) -> AcceptanceReport:
    return AcceptanceReport(
        run_id=command,
        command=command,
        status=status,
        dataset_key=dataset_key,
        version_label=getattr(corpus, "version_label", None),
        corpus_version_id=getattr(corpus, "corpus_version_id", None),
        manifest_hash=getattr(corpus, "manifest_hash", None),
        details=details or {},
    )


def _failure_report(
    *,
    command: str,
    dataset_key: str | None,
    version_label: str | None = None,
    corpus_version_id: UUID | None = None,
    corpus: object | None = None,
    previous_corpus_version_id: UUID | None = None,
    reason: str,
) -> AcceptanceReport:
    report = _report(
        command=command,
        status="failed",
        dataset_key=dataset_key,
        corpus=corpus,
        details={
            "reason": reason,
            "current_corpus_version_id": getattr(
                corpus, "corpus_version_id", corpus_version_id
            ),
            "previous_corpus_version_id": previous_corpus_version_id,
        },
    )
    if version_label is None:
        return report
    return replace(report, version_label=version_label)
