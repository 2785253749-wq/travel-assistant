from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import dataclass, replace
from datetime import date, datetime, timezone
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
    build_import_request,
    credential_states,
    run_import,
    run_incremental_check,
    run_post_activation_smoke,
    run_preflight,
    run_retrieval_check,
    run_same_manifest_check,
    _json_safe,
    validate_dataset_key,
    write_report,
)
from app.rag_v2.acceptance_cases import AcceptanceCase, case_file_sha256
from app.rag_v2.authoring import AuthoringAttraction, AuthoringDocument
from app.rag_v2.hashing import build_embedding_input, content_hash, embedding_input_hash
from app.rag_v2.importer import CorpusImportInput, CorpusImportResult
from app.rag_v2.models import (
    AttractionVersionMetadata,
    AttractionVersionStatus,
    ChunkType,
    Destination,
    DestinationLevel,
    SemanticChunk,
    SemanticSection,
)
from app.rag_v2.repository import CorpusVersion
from app.rag_v2.retrieval import RetrievalEvidence, RetrievalResult


_CORPUS_ID = UUID("00000000-0000-4000-8000-000000000301")
_PREVIOUS_CORPUS_ID = UUID("00000000-0000-4000-8000-000000000302")
_ATTRACTION_ID = UUID("00000000-0000-4000-8000-000000000101")
_DATASET_KEY = "rag-v2-acceptance"


@dataclass
class _FakeChunker:
    chunk_value: SemanticChunk

    def __post_init__(self) -> None:
        self.calls: list[tuple[SemanticSection, AttractionVersionMetadata]] = []

    def chunk(
        self,
        section: SemanticSection,
        *,
        attraction: AttractionVersionMetadata,
    ) -> tuple[SemanticChunk, ...]:
        self.calls.append((section, attraction))
        return (self.chunk_value,)


class _FakeImporter:
    def __init__(self, *results: CorpusImportResult) -> None:
        self.results = list(results)
        self.calls: list[CorpusImportInput] = []

    def import_corpus(self, request: CorpusImportInput) -> CorpusImportResult:
        self.calls.append(request)
        if not self.results:
            raise AssertionError("unexpected importer call")
        return self.results.pop(0)


class _FakeRepository:
    def __init__(
        self,
        *,
        corpus: CorpusVersion | None = None,
        previous_corpus_id: UUID | None = _PREVIOUS_CORPUS_ID,
    ) -> None:
        self.corpus = corpus
        self.previous_corpus_id = previous_corpus_id
        self.events: list[str] = []
        self.activation_calls = 0

    def get_corpus_version(self, *, corpus_version_id: UUID) -> CorpusVersion | None:
        self.events.append(f"read:{corpus_version_id}")
        if self.corpus is not None and corpus_version_id == self.corpus.corpus_version_id:
            return self.corpus
        return None

    def activate_corpus(self, **_: object) -> None:
        self.activation_calls += 1
        raise AssertionError("Task 5 must not activate implicitly")


class _FakeRetrievalService:
    def __init__(self, *results: RetrievalResult) -> None:
        self.results = list(results)
        self.calls: list[dict[str, object]] = []

    def retrieve(self, **kwargs: object) -> RetrievalResult:
        self.calls.append(kwargs)
        if not self.results:
            raise AssertionError("unexpected retrieval call")
        return self.results.pop(0)


class _FakePassageEmbedder:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def embed_passage(self, text: str) -> tuple[float, ...]:
        self.calls.append(text)
        return (0.25,) * 1024


def _destination() -> Destination:
    return Destination(
        destination_code="350200",
        destination_level=DestinationLevel.prefecture_city,
        destination_name="厦门市",
        province_code="350000",
        province_name="福建省",
    )


def _metadata() -> AttractionVersionMetadata:
    return AttractionVersionMetadata(
        attraction_id=_ATTRACTION_ID,
        canonical_name="鼓浪屿",
        aliases=("鼓浪屿",),
        destination=_destination(),
        category="海岛景区",
        tags=("海岛", "步行"),
        status=AttractionVersionStatus.included,
    )


def _section(*, chunk_type: ChunkType = ChunkType.overview) -> SemanticSection:
    return SemanticSection(
        attraction_id=_ATTRACTION_ID,
        chunk_type=chunk_type,
        content="鼓浪屿适合步行游览。",
        source_label="厦门市文化和旅游局",
        source_url="https://wlj.xm.gov.cn/",
        source_type="official",
        reviewed_on=date(2026, 9, 1),
    )


def _chunk(section: SemanticSection | None = None) -> SemanticChunk:
    section = section or _section()
    content = section.content
    embedding_input = build_embedding_input(
        canonical_attraction_name="鼓浪屿",
        destination_name="厦门市",
        destination_code="350200",
        destination_level=DestinationLevel.prefecture_city,
        chunk_type=section.chunk_type,
        normalized_content=content,
    )
    return SemanticChunk(
        chunk_key=(
            f"rag-v2-chunk-key-v1|{_ATTRACTION_ID}|"
            f"{section.chunk_type.value}|0"
        ),
        attraction_id=_ATTRACTION_ID,
        chunk_type=section.chunk_type,
        ordinal=0,
        normalized_content=content,
        content_hash=content_hash(content),
        embedding_input_hash=embedding_input_hash(embedding_input),
        source_label=section.source_label,
        source_url=section.source_url,
        source_type=section.source_type,
        reviewed_on=section.reviewed_on,
    )


def _document() -> AuthoringDocument:
    section = _section()
    return AuthoringDocument(
        schema_version="rag-v2-authoring-v1",
        destination=_destination(),
        attractions=(
            AuthoringAttraction(
                registry_key="xiamen.gulangyu",
                metadata=_metadata(),
                sections=(section,),
            ),
        ),
    )


def _corpus(*, status: str = "staging", corpus_id: UUID = _CORPUS_ID) -> CorpusVersion:
    return CorpusVersion(
        corpus_version_id=corpus_id,
        dataset_key=_DATASET_KEY,
        version_label="v1",
        manifest_hash="a" * 64,
        status=status,
        created_at=datetime(2026, 9, 4),
        activated_at=None,
        superseded_at=None,
    )


def _result(
    *,
    status: str = "staging",
    reused: tuple[str, ...] = (),
    embedded: tuple[str, ...] = (),
) -> CorpusImportResult:
    return CorpusImportResult(
        corpus=_corpus(status=status),
        chunk_rows=(),
        ready_for_activation=status == "staging",
        reused_chunk_keys=reused,
        embedded_chunk_keys=embedded,
    )


def _context(
    *,
    importer: object,
    repository: object | None = None,
    retrieval_service: object | None = None,
    passage_embedder: object | None = None,
) -> AcceptanceContext:
    return AcceptanceContext(
        settings=Settings(_env_file=None),
        repository=repository or _FakeRepository(corpus=_corpus()),
        importer=importer,
        passage_embedder=passage_embedder or _FakePassageEmbedder(),
        query_embedder=SimpleNamespace(),
        retrieval_service=retrieval_service or SimpleNamespace(),
        report_dir=Path(".rag-v2-reports"),
    )


def _request() -> CorpusImportInput:
    section = _section()
    chunker = _FakeChunker(_chunk(section))
    return build_import_request(
        documents=(_document(),),
        dataset_key=_DATASET_KEY,
        version_label="v1",
        corpus_version_id=_CORPUS_ID,
        chunker=chunker,
    )


def _case() -> AcceptanceCase:
    chunk_key = f"rag-v2-chunk-key-v1|{_ATTRACTION_ID}|overview|0"
    return AcceptanceCase(
        case_id="xiamen-gulangyu-exact",
        required=True,
        match_mode="exact",
        query="厦门鼓浪屿有哪些亮点？",
        expected_destination_code="350200",
        attraction_destinations=((_ATTRACTION_ID, "350200"),),
        expected_attraction_ids=(_ATTRACTION_ID,),
        expected_chunk_keys=(chunk_key,),
        expect_no_answer=False,
        require_source_urls=True,
    )


def _retrieval_result(*, evidence: tuple[RetrievalEvidence, ...] = ()) -> RetrievalResult:
    return RetrievalResult(
        query="厦门鼓浪屿有哪些亮点？",
        evidence=evidence,
    )


def _evidence() -> RetrievalEvidence:
    return RetrievalEvidence(
        attraction_id=_ATTRACTION_ID,
        chunk_key=f"rag-v2-chunk-key-v1|{_ATTRACTION_ID}|overview|0",
        chunk_type=ChunkType.overview,
        content="鼓浪屿适合步行游览。",
        content_hash="b" * 64,
        source_label="厦门市文化和旅游局",
        source_url="https://wlj.xm.gov.cn/",
        source_type="official",
        reviewed_on=date(2026, 9, 1),
        score=0.95,
    )


def test_build_import_request_uses_existing_chunker_and_manifest_owners() -> None:
    document = _document()
    chunker = _FakeChunker(_chunk())

    request = build_import_request(
        documents=(document,),
        dataset_key=_DATASET_KEY,
        version_label="v1",
        corpus_version_id=_CORPUS_ID,
        chunker=chunker,
    )

    assert isinstance(request, CorpusImportInput)
    assert request.dataset_key == _DATASET_KEY
    assert request.manifest.schema_version == "rag-v2-manifest-v1"
    assert request.manifest.dataset_key == _DATASET_KEY
    assert request.manifest.embedding_profile.model == "jina-embeddings-v3"
    assert request.manifest.embedding_profile.task == "retrieval.passage"
    assert request.manifest.embedding_profile.dimensions == 1024
    assert len(chunker.calls) == 1
    assert request.attractions[0].metadata == document.attractions[0].metadata


def test_run_import_preserves_staging_result_and_never_activates() -> None:
    expected = _result()
    importer = _FakeImporter(expected)
    repository = _FakeRepository(corpus=_corpus())

    result = run_import(
        context=_context(importer=importer, repository=repository),
        request=_request(),
    )

    assert result is expected
    assert result.corpus.status == "staging"
    assert result.ready_for_activation is True
    assert repository.activation_calls == 0
    assert len(importer.calls) == 1


def test_same_manifest_check_requires_zero_new_passage_calls() -> None:
    importer = _FakeImporter(_result(), _result())
    passage_embedder = _FakePassageEmbedder()
    report = run_same_manifest_check(
        context=_context(importer=importer, passage_embedder=passage_embedder),
        request=_request(),
    )

    assert isinstance(report, AcceptanceReport)
    assert report.status == "passed"
    assert len(importer.calls) == 2
    assert passage_embedder.calls == []


def test_incremental_check_requires_isolated_active_or_superseded_baseline(
    tmp_path: Path,
) -> None:
    patch_path = tmp_path / "incremental-v1.yaml"
    patch_path.write_text("schema_version: rag-v2-authoring-patch-v1\n", encoding="utf-8")
    importer = _FakeImporter()
    repository = _FakeRepository(corpus=_corpus(status="staging"))

    report = run_incremental_check(
        context=_context(importer=importer, repository=repository),
        baseline_documents=(_document(),),
        patch_path=patch_path,
        baseline_corpus_version_id=_CORPUS_ID,
        baseline_version_label="v1",
        next_version_label="v2",
    )

    assert report.status == "failed"
    assert importer.calls == []
    assert repository.activation_calls == 0


def test_incremental_check_reports_reuse_and_selective_reembedding(
    tmp_path: Path,
) -> None:
    patch_path = tmp_path / "incremental-v1.yaml"
    patch_path.write_text(
        "schema_version: rag-v2-authoring-patch-v1\n"
        "target:\n"
        "  destination_code: '350200'\n"
        "  registry_key: xiamen.gulangyu\n"
        "  section: visit_advice\n"
        "replacement_content: 仅用于验收。\n",
        encoding="utf-8",
    )
    importer = _FakeImporter(
        _result(reused=("unchanged",), embedded=("changed",))
    )
    repository = _FakeRepository(corpus=_corpus(status="active"))
    baseline_document = _document()
    baseline_attraction = baseline_document.attractions[0]
    baseline_document = replace(
        baseline_document,
        attractions=(
            replace(
                baseline_attraction,
                sections=(
                    *baseline_attraction.sections,
                    _section(chunk_type=ChunkType.visit_advice),
                ),
            ),
        ),
    )
    assert any(
        section.chunk_type is ChunkType.visit_advice
        for section in baseline_document.attractions[0].sections
    )

    report = run_incremental_check(
        context=_context(importer=importer, repository=repository),
        baseline_documents=(baseline_document,),
        patch_path=patch_path,
        baseline_corpus_version_id=_CORPUS_ID,
        baseline_version_label="v1",
        next_version_label="v2",
    )

    assert report.status == "passed"
    assert report.details["reused_chunk_keys"] == ("unchanged",)
    assert report.details["embedded_chunk_keys"] == ("changed",)
    assert repository.activation_calls == 0
    patched_sections = importer.calls[0].attractions[0].sections
    assert next(
        section.content
        for section in patched_sections
        if section.chunk_type is ChunkType.visit_advice
    ) == "仅用于验收。"


def test_incremental_patch_is_not_written_to_production_authoring(
    tmp_path: Path,
) -> None:
    patch_path = tmp_path / "incremental-v1.yaml"
    patch_bytes = (
        "schema_version: rag-v2-authoring-patch-v1\n"
        "target:\n  destination_code: '350200'\n"
        "  registry_key: xiamen.gulangyu\n  section: visit_advice\n"
        "replacement_content: 仅用于验收。\n"
    ).encode("utf-8")
    patch_path.write_bytes(patch_bytes)
    baseline_document = _document()
    baseline_attraction = baseline_document.attractions[0]
    documents = (
        replace(
            baseline_document,
            attractions=(
                replace(
                    baseline_attraction,
                    sections=(
                        *baseline_attraction.sections,
                        _section(chunk_type=ChunkType.visit_advice),
                    ),
                ),
            ),
        ),
    )
    documents_before = deepcopy(documents)
    importer = _FakeImporter(_result(reused=("unchanged",), embedded=("changed",)))

    run_incremental_check(
        context=_context(
            importer=importer,
            repository=_FakeRepository(corpus=_corpus(status="active")),
        ),
        baseline_documents=documents,
        patch_path=patch_path,
        baseline_corpus_version_id=_CORPUS_ID,
        baseline_version_label="v1",
        next_version_label="v2",
    )

    assert patch_path.read_bytes() == patch_bytes
    assert documents == documents_before
    patched_sections = importer.calls[0].attractions[0].sections
    assert next(
        section.content
        for section in patched_sections
        if section.chunk_type is ChunkType.visit_advice
    ) == "仅用于验收。"


def test_retrieval_check_requires_active_corpus_id() -> None:
    retrieval = _FakeRetrievalService(_retrieval_result(evidence=(_evidence(),)))
    repository = _FakeRepository(corpus=_corpus(status="staging"))

    report = run_retrieval_check(
        context=_context(
            importer=SimpleNamespace(),
            repository=repository,
            retrieval_service=retrieval,
        ),
        cases=(_case(),),
        dataset_key=_DATASET_KEY,
        required_active_corpus_id=_CORPUS_ID,
    )

    assert report.status == "failed"
    assert retrieval.calls == []


def test_retrieval_check_maps_required_observations_and_case_hash() -> None:
    retrieval = _FakeRetrievalService(_retrieval_result(evidence=(_evidence(),)))
    repository = _FakeRepository(corpus=_corpus(status="active"))

    report = run_retrieval_check(
        context=_context(
            importer=SimpleNamespace(),
            repository=repository,
            retrieval_service=retrieval,
        ),
        cases=(_case(),),
        dataset_key=_DATASET_KEY,
        required_active_corpus_id=_CORPUS_ID,
    )

    assert report.status == "passed"
    assert report.details["required_cases_passed"] == 1
    assert report.details["required_cases_failed"] == 0
    assert report.details["observations"][0].case.case_id == "xiamen-gulangyu-exact"


def test_retrieval_check_reports_case_set_identity_and_sha256(tmp_path: Path) -> None:
    case_file = tmp_path / "cases-v1.jsonl"
    case_bytes = b'{"case_id":"xiamen-gulangyu-exact"}\n'
    case_file.write_bytes(case_bytes)
    retrieval = _FakeRetrievalService(_retrieval_result(evidence=(_evidence(),)))
    repository = _FakeRepository(corpus=_corpus(status="active"))

    report = run_retrieval_check(
        context=_context(
            importer=SimpleNamespace(),
            repository=repository,
            retrieval_service=retrieval,
        ),
        cases=(_case(),),
        dataset_key=_DATASET_KEY,
        required_active_corpus_id=_CORPUS_ID,
        case_set_id="cases-v1",
        case_file_path=case_file,
    )

    assert report.details["case_set_id"] == "cases-v1"
    assert report.details["case_file_sha256"] == case_file_sha256(case_file)


def test_incremental_check_rejects_active_baseline_from_wrong_dataset(
    tmp_path: Path,
) -> None:
    patch_path = tmp_path / "incremental-v1.yaml"
    patch_path.write_text(
        "schema_version: rag-v2-authoring-patch-v1\n"
        "target:\n"
        "  destination_code: '350200'\n"
        "  registry_key: xiamen.gulangyu\n"
        "  section: visit_advice\n"
        "replacement_content: 仅用于验收。\n",
        encoding="utf-8",
    )
    importer = _FakeImporter(_result())
    repository = _FakeRepository(
        corpus=replace(_corpus(status="active"), dataset_key="rag-v2-production")
    )

    report = run_incremental_check(
        context=_context(importer=importer, repository=repository),
        baseline_documents=(_document(),),
        patch_path=patch_path,
        baseline_corpus_version_id=_CORPUS_ID,
        baseline_version_label="v1",
        next_version_label="v2",
    )

    assert report.status == "failed"
    assert importer.calls == []


def test_incremental_check_rejects_missing_patch_target(tmp_path: Path) -> None:
    patch_path = tmp_path / "incremental-v1.yaml"
    patch_path.write_text(
        "schema_version: rag-v2-authoring-patch-v1\n"
        "target:\n"
        "  destination_code: '350200'\n"
        "  registry_key: xiamen.gulangyu\n"
        "  section: visit_advice\n"
        "replacement_content: 仅用于验收。\n",
        encoding="utf-8",
    )
    repository = _FakeRepository(corpus=_corpus(status="active"))

    with pytest.raises(ValueError):
        run_incremental_check(
            context=_context(
                importer=_FakeImporter(_result()),
                repository=repository,
            ),
            baseline_documents=(_document(),),
            patch_path=patch_path,
            baseline_corpus_version_id=_CORPUS_ID,
            baseline_version_label="v1",
            next_version_label="v2",
        )


def test_retrieval_check_blocks_completion_on_required_failure() -> None:
    retrieval = _FakeRetrievalService(_retrieval_result())
    repository = _FakeRepository(corpus=_corpus(status="active"))

    report = run_retrieval_check(
        context=_context(
            importer=SimpleNamespace(),
            repository=repository,
            retrieval_service=retrieval,
        ),
        cases=(_case(),),
        dataset_key=_DATASET_KEY,
        required_active_corpus_id=_CORPUS_ID,
    )

    assert report.status == "failed"
    assert report.details["required_cases_failed"] == 1


def test_post_activation_smoke_uses_active_dataset() -> None:
    retrieval = _FakeRetrievalService(_retrieval_result(evidence=(_evidence(),)))
    repository = _FakeRepository(corpus=_corpus(status="active"))

    report = run_post_activation_smoke(
        context=_context(
            importer=SimpleNamespace(),
            repository=repository,
            retrieval_service=retrieval,
        ),
        cases=(_case(),),
        dataset_key="rag-v2-production",
    )

    assert report.status == "passed"
    assert retrieval.calls
    assert all(call["dataset_key"] == "rag-v2-production" for call in retrieval.calls)


def test_import_or_retrieval_failure_reports_current_and_previous_corpus_without_rollback() -> None:
    retrieval = _FakeRetrievalService()
    repository = _FakeRepository(
        corpus=_corpus(status="active"),
        previous_corpus_id=_PREVIOUS_CORPUS_ID,
    )

    report = run_post_activation_smoke(
        context=_context(
            importer=SimpleNamespace(),
            repository=repository,
            retrieval_service=retrieval,
        ),
        cases=(_case(),),
        dataset_key="rag-v2-production",
    )

    assert report.status == "failed"
    assert report.details["current_corpus_version_id"] == _CORPUS_ID
    assert report.details["previous_corpus_version_id"] == _PREVIOUS_CORPUS_ID
    assert repository.activation_calls == 0


_TASK4_CORPUS_ID = UUID("00000000-0000-4000-8000-000000000201")
_TASK4_JINA_SECRET = "unit-only-jina-secret"
_TASK4_SUPABASE_SECRET = "unit-only-supabase-secret"


class _Task4AcceptancePassageEmbedder:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def embed_passage(self, text: str) -> tuple[float, ...]:
        self.calls.append(text)
        return (0.0,) * 1024


class _Task4AcceptanceQueryEmbedder:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def embed_query(self, query: str) -> tuple[float, ...]:
        self.calls.append(query)
        return (0.0,) * 1024


class _Task4AcceptanceRepository:
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


def _task4_settings() -> Settings:
    return Settings(
        _env_file=None,
        jina_api_key=SecretStr(_TASK4_JINA_SECRET),
        supabase_url="https://project.supabase.co",
        supabase_service_key=SecretStr(_TASK4_SUPABASE_SECRET),
    )


def _task4_context(
    tmp_path: Path,
    repository: _Task4AcceptanceRepository | None = None,
) -> AcceptanceContext:
    return AcceptanceContext(
        settings=_task4_settings(),
        repository=repository or _Task4AcceptanceRepository(),
        importer=SimpleNamespace(),
        passage_embedder=_Task4AcceptancePassageEmbedder(),
        query_embedder=_Task4AcceptanceQueryEmbedder(),
        retrieval_service=SimpleNamespace(),
        report_dir=tmp_path / ".rag-v2-reports",
    )


def _task4_report(**details: object) -> AcceptanceReport:
    return AcceptanceReport(
        run_id="run-001",
        command="preflight",
        status="failed",
        dataset_key="rag-v2-acceptance",
        version_label="v1",
        corpus_version_id=_TASK4_CORPUS_ID,
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
    states = credential_states(_task4_settings())
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
    assert _TASK4_JINA_SECRET not in serialized
    assert _TASK4_SUPABASE_SECRET not in serialized


def test_report_serialization_is_secret_safe(tmp_path: Path) -> None:
    report = _task4_report(
        credential_states=(
            {"name": "JINA_API_KEY", "state": "SET"},
            {"name": "SUPABASE_SERVICE_KEY", "state": "SET"},
        )
    )

    path = write_report(report=report, report_dir=tmp_path / ".rag-v2-reports")
    serialized = path.read_text(encoding="utf-8")

    assert "JINA_API_KEY" in serialized
    assert '"state": "SET"' in serialized
    assert _TASK4_JINA_SECRET not in serialized
    assert _TASK4_SUPABASE_SECRET not in serialized


def test_preflight_uses_non_production_probe_key(tmp_path: Path) -> None:
    repository = _Task4AcceptanceRepository()

    result = run_preflight(context=_task4_context(tmp_path, repository))

    assert isinstance(result, PreflightResult)
    assert repository.match_calls
    assert repository.match_calls[0]["dataset_key"] == "rag-v2-preflight"
    assert repository.match_calls[0]["dataset_key"] not in DATASET_KEYS.values()


def test_preflight_does_not_call_activation(tmp_path: Path) -> None:
    repository = _Task4AcceptanceRepository()

    run_preflight(context=_task4_context(tmp_path, repository))

    assert repository.activation_calls == 0


def test_preflight_reports_missing_prerequisite_without_ddl(tmp_path: Path) -> None:
    repository = _Task4AcceptanceRepository(
        read_failure=RuntimeError("raw database prerequisite secret")
    )

    result = run_preflight(context=_task4_context(tmp_path, repository))

    assert isinstance(result, PreflightResult)
    assert result.failures
    assert all(
        "raw database prerequisite secret" not in failure
        for failure in result.failures
    )
    assert repository.activation_calls == 0


def test_report_path_is_local_and_ignored(tmp_path: Path) -> None:
    report_dir = tmp_path / ".rag-v2-reports"
    path = write_report(report=_task4_report(), report_dir=report_dir)
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
        corpus_version_id=_TASK4_CORPUS_ID,
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
    assert payload["corpus_version_id"] == str(_TASK4_CORPUS_ID)
    assert payload["manifest_hash"] == "b" * 64
    assert payload["details"] == {
        "ready_for_activation": True,
        "required_cases_passed": 10,
    }


def test_json_safe_serializes_nested_date_and_datetime_values() -> None:
    @dataclass(frozen=True)
    class TemporalValues:
        created_at: datetime
        reviewed_on: date

    value = TemporalValues(
        created_at=datetime(
            2026,
            9,
            6,
            12,
            34,
            56,
            tzinfo=timezone.utc,
        ),
        reviewed_on=date(2026, 9, 6),
    )

    assert _json_safe({"result": value}) == {
        "result": {
            "created_at": "2026-09-06T12:34:56+00:00",
            "reviewed_on": "2026-09-06",
        }
    }


def test_failed_report_contains_no_raw_upstream_text(tmp_path: Path) -> None:
    report = _task4_report(
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
