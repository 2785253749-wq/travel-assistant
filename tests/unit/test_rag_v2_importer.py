from __future__ import annotations

from dataclasses import FrozenInstanceError, fields, replace
from datetime import date, datetime, timezone
from importlib import import_module
from uuid import UUID

import pytest

from app.core.errors import AppError
from app.rag_v2.chunking import chunk_key_for
from app.rag_v2.hashing import (
    build_embedding_input,
    content_hash,
    embedding_input_hash,
    manifest_hash,
    metadata_hash,
    normalize_content,
)
from app.rag_v2.models import (
    AttractionVersionMetadata,
    AttractionVersionStatus,
    ChunkStatus,
    ChunkType,
    Destination,
    DestinationLevel,
    EmbeddingProfile,
    EmbeddingTask,
    ManifestAttraction,
    ManifestChunk,
    ManifestInput,
    SemanticChunk,
    SemanticSection,
)
from app.rag_v2.repository import (
    AttractionVersionRecord,
    ChunkInsert,
    ChunkRow,
    CorpusVersion,
)


_CORPUS_ID = UUID("00000000-0000-0000-0000-000000000010")
_CANDIDATE_ID = UUID("00000000-0000-0000-0000-000000000011")
_ALLOCATED_ID = UUID("00000000-0000-0000-0000-000000000012")
_OTHER_ID = UUID("00000000-0000-0000-0000-000000000013")
_REVIEWED_ON = date(2026, 1, 2)
_CREATED_AT = datetime(2026, 1, 1, tzinfo=timezone.utc)
_DATASET_KEY = "china-attractions"
_VERSION_LABEL = "2026-01"


def _importer_module():
    return import_module("app.rag_v2.importer")


def _metadata(
    attraction_id: UUID = _CANDIDATE_ID,
    *,
    status: AttractionVersionStatus = AttractionVersionStatus.included,
) -> AttractionVersionMetadata:
    return AttractionVersionMetadata(
        attraction_id=attraction_id,
        canonical_name="鼓浪屿",
        aliases=("琴岛",),
        destination=Destination(
            destination_code="350200",
            destination_level=DestinationLevel.prefecture_city,
            destination_name="厦门市",
            province_code="350000",
            province_name="福建省",
            district_name="思明区",
            latitude=24.4489,
            longitude=118.0646,
        ),
        category="island",
        tags=("海岛", "文化"),
        status=status,
    )


def _section(
    attraction_id: UUID = _CANDIDATE_ID,
    *,
    chunk_type: ChunkType = ChunkType.overview,
    content: str = "鼓浪屿位于厦门市。",
    source_label: str = "官方旅游网站",
    source_url: str = "https://example.com/gulangyu",
    source_type: str = "official",
    reviewed_on: date = _REVIEWED_ON,
) -> SemanticSection:
    return SemanticSection(
        attraction_id=attraction_id,
        chunk_type=chunk_type,
        content=content,
        source_label=source_label,
        source_url=source_url,
        source_type=source_type,
        reviewed_on=reviewed_on,
    )


def _chunk(
    attraction_id: UUID = _CANDIDATE_ID,
    *,
    section: SemanticSection | None = None,
    ordinal: int = 0,
) -> SemanticChunk:
    section = section or _section(attraction_id)
    normalized_content = normalize_content(section.content)
    embedding_input = build_embedding_input(
        canonical_attraction_name=_metadata(attraction_id).canonical_name,
        destination_name=_metadata(attraction_id).destination.destination_name,
        destination_code=_metadata(attraction_id).destination.destination_code,
        destination_level=_metadata(attraction_id).destination.destination_level,
        chunk_type=section.chunk_type,
        normalized_content=normalized_content,
    )
    return SemanticChunk(
        chunk_key=chunk_key_for(
            attraction_id=attraction_id,
            chunk_type=section.chunk_type,
            ordinal=ordinal,
        ),
        attraction_id=attraction_id,
        chunk_type=section.chunk_type,
        ordinal=ordinal,
        normalized_content=normalized_content,
        content_hash=content_hash(normalized_content),
        embedding_input_hash=embedding_input_hash(embedding_input),
        source_label=section.source_label,
        source_url=section.source_url,
        source_type=section.source_type,
        reviewed_on=section.reviewed_on,
    )


def _manifest(
    *,
    attraction_id: UUID = _CANDIDATE_ID,
    dataset_key: str = _DATASET_KEY,
    chunk: SemanticChunk | None = None,
    metadata: AttractionVersionMetadata | None = None,
) -> ManifestInput:
    metadata = metadata or _metadata(attraction_id)
    chunk = chunk or _chunk(attraction_id)
    return ManifestInput(
        schema_version="rag-v2-manifest-v1",
        dataset_key=dataset_key,
        embedding_profile=EmbeddingProfile(
            model="jina-embeddings-v3",
            task=EmbeddingTask.passage,
            dimensions=1024,
            input_schema_version="rag-v2-embedding-input-v1",
        ),
        attractions=(
            ManifestAttraction(
                attraction_id=attraction_id,
                metadata_hash=metadata_hash(metadata),
                chunks=(
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
                    ),
                ),
            ),
        ),
    )


def _request(
    *,
    metadata: AttractionVersionMetadata | None = None,
    section: SemanticSection | None = None,
    manifest: ManifestInput | None = None,
    attractions: tuple[object, ...] | None = None,
    dataset_key: str = _DATASET_KEY,
    version_label: str = _VERSION_LABEL,
    retry_failed: bool = False,
):
    module = _importer_module()
    metadata = metadata or _metadata()
    section = section or _section(metadata.attraction_id)
    if manifest is None:
        chunk = _chunk(metadata.attraction_id, section=section)
        manifest = _manifest(
            attraction_id=metadata.attraction_id,
            dataset_key=dataset_key,
            chunk=chunk,
            metadata=metadata,
        )
    import_attractions = attractions or (
        module.ImportAttraction(
            registry_key="xiamen:gulangyu",
            metadata=metadata,
            sections=(section,),
        ),
    )
    return module.CorpusImportInput(
        corpus_version_id=_CORPUS_ID,
        dataset_key=dataset_key,
        version_label=version_label,
        manifest=manifest,
        attractions=import_attractions,
        retry_failed=retry_failed,
    )


def _corpus(
    status: str,
    *,
    corpus_version_id: UUID = _CORPUS_ID,
    manifest_hash_value: str | None = None,
) -> CorpusVersion:
    return CorpusVersion(
        corpus_version_id=corpus_version_id,
        dataset_key=_DATASET_KEY,
        version_label=_VERSION_LABEL,
        manifest_hash=manifest_hash_value or manifest_hash(_manifest()),
        status=status,
        created_at=_CREATED_AT,
        activated_at=None,
        superseded_at=None,
    )


def _chunk_row(chunk: SemanticChunk, *, corpus_version_id: UUID = _CORPUS_ID) -> ChunkRow:
    return ChunkRow(
        corpus_version_id=corpus_version_id,
        attraction_id=chunk.attraction_id,
        chunk_key=chunk.chunk_key,
        chunk_type=chunk.chunk_type,
        ordinal=chunk.ordinal,
        content=chunk.normalized_content,
        content_hash=chunk.content_hash,
        embedding_input_hash=chunk.embedding_input_hash,
        embedding_input_schema_version="rag-v2-embedding-input-v1",
        source_label=chunk.source_label,
        source_url=chunk.source_url,
        source_type=chunk.source_type,
        reviewed_on=chunk.reviewed_on,
        embedding_model="jina-embeddings-v3",
        embedding_task="retrieval.passage",
        embedding_dimensions=1024,
        embedding=None,
        status=ChunkStatus.pending,
        embedding_error_code=None,
        embedding_error_message=None,
    )


class FakeIdentitySource:
    def __init__(self, *, resolutions: dict[str, UUID | None] | None = None) -> None:
        self.resolutions = resolutions or {}
        self.resolve_calls: list[str] = []
        self.allocate_calls: list[str] = []

    def resolve(self, registry_key: str) -> UUID | None:
        self.resolve_calls.append(registry_key)
        return self.resolutions.get(registry_key)

    def allocate(self, registry_key: str) -> UUID:
        self.allocate_calls.append(registry_key)
        return _ALLOCATED_ID


class FakeChunker:
    def __init__(
        self,
        *,
        chunks: tuple[SemanticChunk, ...],
        chunks_by_attraction: dict[UUID, tuple[SemanticChunk, ...]] | None = None,
    ) -> None:
        self.chunks = chunks
        self.chunks_by_attraction = chunks_by_attraction or {}
        self.calls: list[dict[str, object]] = []

    def chunk(
        self,
        section: SemanticSection,
        *,
        attraction: AttractionVersionMetadata,
    ) -> tuple[SemanticChunk, ...]:
        self.calls.append({"section": section, "attraction": attraction})
        return self.chunks_by_attraction.get(section.attraction_id, self.chunks)


class FakePassageEmbedder:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def embed_passage(self, text: str) -> tuple[float, ...]:
        self.calls.append(text)
        raise AssertionError("Task 3 must not call the passage embedder")


class FakeRepository:
    def __init__(
        self,
        *,
        corpus: CorpusVersion,
        attraction_versions: tuple[AttractionVersionRecord, ...] = (),
        chunk_rows: tuple[ChunkRow, ...] = (),
        create_error: AppError | None = None,
        snapshot_error: AppError | None = None,
    ) -> None:
        self.corpus = corpus
        self.attraction_versions = attraction_versions
        self.chunk_rows = chunk_rows
        self.create_error = create_error
        self.snapshot_error = snapshot_error
        self.create_calls: list[dict[str, object]] = []
        self.attraction_version_calls: list[UUID] = []
        self.chunk_row_calls: list[UUID] = []
        self.inserted_attraction_versions: list[
            tuple[AttractionVersionRecord, ...]
        ] = []
        self.inserted_chunks: list[tuple[ChunkInsert, ...]] = []
        self.inserted_chunk_rows: list[tuple[ChunkRow, ...]] = []
        self.mark_failed_calls: list[UUID] = []
        self.operation_log: list[str] = []
        self.forbidden_calls: list[str] = []

    def create_corpus_version(self, **kwargs: object) -> CorpusVersion:
        self.operation_log.append("create_corpus_version")
        self.create_calls.append(kwargs)
        if self.create_error is not None:
            raise self.create_error
        return self.corpus

    def list_attraction_versions(
        self,
        *,
        corpus_version_id: UUID,
    ) -> tuple[AttractionVersionRecord, ...]:
        self.operation_log.append("list_attraction_versions")
        self.attraction_version_calls.append(corpus_version_id)
        if self.snapshot_error is not None:
            raise self.snapshot_error
        return self.attraction_versions

    def list_chunk_rows(self, *, corpus_version_id: UUID) -> tuple[ChunkRow, ...]:
        self.operation_log.append("list_chunk_rows")
        self.chunk_row_calls.append(corpus_version_id)
        if self.snapshot_error is not None:
            raise self.snapshot_error
        return self.chunk_rows

    def insert_attraction_versions(
        self,
        records: tuple[AttractionVersionRecord, ...],
    ) -> tuple[AttractionVersionRecord, ...]:
        self.operation_log.append("insert_attraction_versions")
        inserted = tuple(records)
        self.inserted_attraction_versions.append(inserted)
        return inserted

    def insert_chunks(
        self,
        inserts: tuple[ChunkInsert, ...],
    ) -> tuple[ChunkRow, ...]:
        self.operation_log.append("insert_chunks")
        inserted = tuple(inserts)
        self.inserted_chunks.append(inserted)
        rows = tuple(
            _chunk_row(item.chunk, corpus_version_id=item.corpus_version_id)
            for item in inserted
        )
        self.inserted_chunk_rows.append(rows)
        return rows

    def mark_corpus_failed(self, *, corpus_version_id: UUID) -> CorpusVersion:
        self.operation_log.append("mark_corpus_failed")
        self.mark_failed_calls.append(corpus_version_id)
        return replace(self.corpus, status="failed")

    def __getattr__(self, name: str) -> object:
        if name in {
            "list_embedded_chunks_for_reuse",
            "decide_incremental",
            "embed_passage",
            "mark_chunk_embedded",
            "mark_chunk_embedding_failed",
            "reset_chunk_embedding_for_retry",
            "activate_corpus",
        }:
            self.forbidden_calls.append(name)
            raise AssertionError(f"Task 4 must not call {name}")
        raise AttributeError(name)


def _make_importer(
    *,
    repository: FakeRepository,
    identity_source: FakeIdentitySource | None = None,
    chunker: FakeChunker | None = None,
):
    module = _importer_module()
    return module.RagV2Importer(
        identity_source=identity_source or FakeIdentitySource(
            resolutions={"xiamen:gulangyu": _CANDIDATE_ID}
        ),
        repository=repository,
        chunker=chunker or FakeChunker(chunks=(_chunk(),)),
        passage_embedder=FakePassageEmbedder(),
    )


def test_task3_dataclasses_have_exact_frozen_contracts_and_empty_sections_are_valid() -> None:
    module = _importer_module()
    corpus = _corpus("staging")
    chunk = _chunk()
    metadata = _metadata()
    section = _section()
    attraction = module.ImportAttraction(
        registry_key="xiamen:gulangyu",
        metadata=metadata,
        sections=(),
    )
    request = module.CorpusImportInput(
        corpus_version_id=_CORPUS_ID,
        dataset_key=_DATASET_KEY,
        version_label=_VERSION_LABEL,
        manifest=_manifest(),
        attractions=(attraction,),
    )
    result = module.CorpusImportResult(
        corpus=corpus,
        chunk_rows=(_chunk_row(chunk),),
        ready_for_activation=False,
        reused_chunk_keys=(),
        embedded_chunk_keys=(),
    )

    assert tuple(field.name for field in fields(attraction)) == (
        "registry_key",
        "metadata",
        "sections",
    )
    assert tuple(field.name for field in fields(request)) == (
        "corpus_version_id",
        "dataset_key",
        "version_label",
        "manifest",
        "attractions",
        "retry_failed",
    )
    assert tuple(field.name for field in fields(result)) == (
        "corpus",
        "chunk_rows",
        "ready_for_activation",
        "reused_chunk_keys",
        "embedded_chunk_keys",
    )
    with pytest.raises(FrozenInstanceError):
        request.dataset_key = "other"
    assert section.reviewed_on == _REVIEWED_ON


def test_task3_importer_constructor_and_method_have_only_approved_dependencies() -> None:
    import inspect

    constructor_parameters = tuple(
        inspect.signature(_importer_module().RagV2Importer.__init__).parameters
    )
    import_parameters = tuple(
        inspect.signature(_importer_module().RagV2Importer.import_corpus).parameters
    )

    assert constructor_parameters == (
        "self",
        "identity_source",
        "repository",
        "chunker",
        "passage_embedder",
    )
    assert import_parameters == ("self", "request")


@pytest.mark.parametrize(
    ("source_label", "source_type", "source_url"),
    [
        ("", "official", "https://example.com/source"),
        ("   ", "official", "https://example.com/source"),
        ("Official", "", "https://example.com/source"),
        ("Official", "   ", "https://example.com/source"),
        ("Official", "official", "example.com/source"),
        ("Official", "official", "http://example.com/source"),
        ("Official", "official", "https://"),
        ("Official", "official", "https://user@example.com/source"),
        ("Official", "official", "https://user:secret@example.com/source"),
    ],
)
def test_task3_rejects_incomplete_structural_source_provenance(
    source_label: str,
    source_type: str,
    source_url: str,
) -> None:
    section = _section(
        source_label=source_label,
        source_type=source_type,
        source_url=source_url,
    )
    request = _request(section=section)
    repository = FakeRepository(corpus=_corpus("staging"))

    with pytest.raises(ValueError):
        _make_importer(repository=repository).import_corpus(request)

    assert repository.create_calls == []


def test_task3_accepts_structurally_complete_https_source() -> None:
    request = _request(section=_section(source_url="https://docs.example.org/guide"))
    repository = FakeRepository(
        corpus=_corpus(
            "active",
            manifest_hash_value=manifest_hash(request.manifest),
        )
    )

    result = _make_importer(repository=repository).import_corpus(request)

    assert result.ready_for_activation is False


def test_task3_rejects_duplicate_registry_keys_before_persistence() -> None:
    module = _importer_module()
    first = module.ImportAttraction(
        registry_key="duplicate",
        metadata=_metadata(_CANDIDATE_ID),
        sections=(_section(_CANDIDATE_ID),),
    )
    second = module.ImportAttraction(
        registry_key="duplicate",
        metadata=_metadata(_OTHER_ID),
        sections=(_section(_OTHER_ID),),
    )
    request = _request(attractions=(first, second))
    repository = FakeRepository(corpus=_corpus("staging"))

    with pytest.raises(ValueError):
        _make_importer(repository=repository).import_corpus(request)

    assert repository.create_calls == []


def test_task3_rejects_duplicate_section_chunk_types_before_persistence() -> None:
    first = _section(chunk_type=ChunkType.overview)
    second = _section(chunk_type=ChunkType.overview, content="另一段内容。")
    request = _request(section=first)
    request = request.__class__(
        corpus_version_id=request.corpus_version_id,
        dataset_key=request.dataset_key,
        version_label=request.version_label,
        manifest=request.manifest,
        attractions=(
            _importer_module().ImportAttraction(
                registry_key="xiamen:gulangyu",
                metadata=_metadata(),
                sections=(first, second),
            ),
        ),
    )
    repository = FakeRepository(corpus=_corpus("staging"))

    with pytest.raises(ValueError):
        _make_importer(repository=repository).import_corpus(request)

    assert repository.create_calls == []


def test_task3_rejects_existing_identity_mismatch() -> None:
    request = _request()
    identity = FakeIdentitySource(resolutions={"xiamen:gulangyu": _OTHER_ID})
    repository = FakeRepository(corpus=_corpus("staging"))

    with pytest.raises(ValueError):
        _make_importer(
            repository=repository,
            identity_source=identity,
        ).import_corpus(request)

    assert repository.create_calls == []
    assert identity.allocate_calls == []


def test_task3_rejects_request_dataset_mismatch_with_manifest() -> None:
    request = _request(
        dataset_key="other-dataset",
        manifest=_manifest(dataset_key=_DATASET_KEY),
    )
    repository = FakeRepository(corpus=_corpus("staging"))

    with pytest.raises(ValueError):
        _make_importer(repository=repository).import_corpus(request)

    assert repository.create_calls == []


@pytest.mark.parametrize(
    ("dataset_key", "version_label"),
    [
        ("", _VERSION_LABEL),
        (_DATASET_KEY, ""),
    ],
)
def test_task3_rejects_empty_request_identity(
    dataset_key: str,
    version_label: str,
) -> None:
    request = _request(
        dataset_key=dataset_key,
        version_label=version_label,
        manifest=_manifest(dataset_key=dataset_key),
    )
    repository = FakeRepository(corpus=_corpus("staging"))

    with pytest.raises(ValueError):
        _make_importer(repository=repository).import_corpus(request)

    assert repository.create_calls == []


def test_task3_rejects_non_boolean_retry_flag() -> None:
    request = _request(retry_failed=1)  # type: ignore[arg-type]
    repository = FakeRepository(corpus=_corpus("staging"))

    with pytest.raises(ValueError):
        _make_importer(repository=repository).import_corpus(request)

    assert repository.create_calls == []


def test_task3_rejects_missing_reviewed_on_when_typed_section_can_be_constructed() -> None:
    section = SemanticSection.model_construct(
        attraction_id=_CANDIDATE_ID,
        chunk_type=ChunkType.overview,
        content="鼓浪屿位于厦门市。",
        source_label="官方旅游网站",
        source_url="https://example.com/gulangyu",
        source_type="official",
        reviewed_on=None,
    )
    request = _request(section=section, manifest=_manifest())
    repository = FakeRepository(corpus=_corpus("staging"))

    with pytest.raises(ValueError):
        _make_importer(repository=repository).import_corpus(request)

    assert repository.create_calls == []


def test_task3_rejects_manifest_profile_mutation() -> None:
    manifest = _manifest().model_copy(
        update={
            "embedding_profile": EmbeddingProfile(
                model="jina-embeddings-v3",
                task=EmbeddingTask.query,
                dimensions=1024,
                input_schema_version="rag-v2-embedding-input-v1",
            )
        }
    )
    request = _request(manifest=manifest)
    repository = FakeRepository(corpus=_corpus("staging"))

    with pytest.raises(ValueError):
        _make_importer(repository=repository).import_corpus(request)

    assert repository.create_calls == []


def test_task3_rejects_manifest_hash_artifact_mismatch() -> None:
    chunk = _chunk()
    manifest = _manifest(
        chunk=chunk.model_copy(update={"content_hash": "b" * 64})
    )
    request = _request(manifest=manifest)
    repository = FakeRepository(corpus=_corpus("staging"))

    with pytest.raises(ValueError):
        _make_importer(repository=repository).import_corpus(request)

    assert repository.create_calls == []


@pytest.mark.parametrize("status", ["active", "superseded"])
def test_task3_same_manifest_terminal_corpus_is_completed_noop(status: str) -> None:
    request = _request()
    repository = FakeRepository(corpus=_corpus(status))
    passage_embedder = FakePassageEmbedder()
    importer = _importer_module().RagV2Importer(
        identity_source=FakeIdentitySource(
            resolutions={"xiamen:gulangyu": _CANDIDATE_ID}
        ),
        repository=repository,
        chunker=FakeChunker(chunks=(_chunk(),)),
        passage_embedder=passage_embedder,
    )

    result = importer.import_corpus(request)

    assert result.corpus.status == status
    assert result.corpus.corpus_version_id == _CORPUS_ID
    assert result.chunk_rows == ()
    assert result.ready_for_activation is False
    assert result.reused_chunk_keys == ()
    assert result.embedded_chunk_keys == ()
    assert repository.attraction_version_calls == []
    assert repository.chunk_row_calls == []
    assert passage_embedder.calls == []


def test_task3_failed_corpus_conflicts_without_resurrection() -> None:
    request = _request()
    repository = FakeRepository(corpus=_corpus("failed"))

    with pytest.raises(AppError) as raised:
        _make_importer(repository=repository).import_corpus(request)

    assert raised.value.code == "RAG_V2_VERSION_CONFLICT"
    assert repository.attraction_version_calls == []
    assert repository.chunk_row_calls == []


def test_task3_staging_reads_authoritative_snapshot_and_returns_authoritative_id() -> None:
    manifest_id = _CANDIDATE_ID
    metadata = _metadata(manifest_id)
    section = _section(manifest_id)
    chunk = _chunk(manifest_id, section=section)
    version = AttractionVersionRecord(
        corpus_version_id=_OTHER_ID,
        metadata=metadata,
        metadata_hash=metadata_hash(metadata),
    )
    row = _chunk_row(chunk, corpus_version_id=_OTHER_ID)
    authoritative_corpus = _corpus("staging", corpus_version_id=_OTHER_ID)
    request = _request(metadata=metadata, section=section)
    repository = FakeRepository(
        corpus=authoritative_corpus,
        attraction_versions=(version,),
        chunk_rows=(row,),
    )
    importer = _make_importer(
        repository=repository,
        chunker=FakeChunker(chunks=(chunk,)),
    )

    result = importer.import_corpus(request)

    assert result.corpus is authoritative_corpus
    assert result.corpus.corpus_version_id == _OTHER_ID
    assert result.chunk_rows == (row,)
    assert repository.create_calls == [
        {
            "corpus_version_id": _CORPUS_ID,
            "dataset_key": _DATASET_KEY,
            "version_label": _VERSION_LABEL,
            "manifest_hash": manifest_hash(request.manifest),
        }
    ]
    assert repository.attraction_version_calls == [_OTHER_ID]
    assert repository.chunk_row_calls == [_OTHER_ID]
    assert result.ready_for_activation is False


def test_task3_allocates_unresolved_identity_once_and_adapts_prepared_snapshot() -> None:
    metadata = _metadata(_CANDIDATE_ID)
    section = _section(_CANDIDATE_ID)
    allocated_chunk = _chunk(_ALLOCATED_ID, section=_section(_ALLOCATED_ID))
    request = _request(
        metadata=metadata,
        section=section,
        manifest=_manifest(
            attraction_id=_ALLOCATED_ID,
            chunk=allocated_chunk,
            metadata=metadata,
        ),
    )
    identity = FakeIdentitySource(resolutions={"xiamen:gulangyu": None})
    repository = FakeRepository(
        corpus=_corpus(
            "staging",
            manifest_hash_value=manifest_hash(request.manifest),
        )
    )
    chunker = FakeChunker(chunks=(allocated_chunk,))

    _make_importer(
        repository=repository,
        identity_source=identity,
        chunker=chunker,
    ).import_corpus(request)

    assert identity.resolve_calls == ["xiamen:gulangyu"]
    assert identity.allocate_calls == ["xiamen:gulangyu"]
    assert chunker.calls[0]["attraction"].attraction_id == _ALLOCATED_ID


def test_task3_preserves_repository_app_error_identity_without_failure_mutation() -> None:
    request = _request()
    original_error = AppError("RAG_V2_UNAVAILABLE", "RAG V2 persistence is unavailable")
    repository = FakeRepository(
        corpus=_corpus("staging"),
        create_error=original_error,
    )

    with pytest.raises(AppError) as raised:
        _make_importer(repository=repository).import_corpus(request)

    assert raised.value is original_error
    assert repository.attraction_version_calls == []
    assert repository.chunk_row_calls == []


def _version_record(
    metadata: AttractionVersionMetadata,
    *,
    corpus_version_id: UUID = _CORPUS_ID,
) -> AttractionVersionRecord:
    return AttractionVersionRecord(
        corpus_version_id=corpus_version_id,
        metadata=metadata,
        metadata_hash=metadata_hash(metadata),
    )


def _manifest_for_entries(
    entries: tuple[
        tuple[AttractionVersionMetadata, tuple[SemanticChunk, ...]],
        ...,
    ],
) -> ManifestInput:
    return ManifestInput(
        schema_version="rag-v2-manifest-v1",
        dataset_key=_DATASET_KEY,
        embedding_profile=EmbeddingProfile(
            model="jina-embeddings-v3",
            task=EmbeddingTask.passage,
            dimensions=1024,
            input_schema_version="rag-v2-embedding-input-v1",
        ),
        attractions=tuple(
            ManifestAttraction(
                attraction_id=metadata.attraction_id,
                metadata_hash=metadata_hash(metadata),
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
            for metadata, chunks in entries
        ),
    )


def _task4_request(
    entries: tuple[
        tuple[
            str,
            AttractionVersionMetadata,
            tuple[SemanticSection, ...],
            tuple[SemanticChunk, ...],
        ],
        ...,
    ],
):
    module = _importer_module()
    manifest = _manifest_for_entries(
        tuple((metadata, chunks) for _, metadata, _, chunks in entries)
    )
    request = module.CorpusImportInput(
        corpus_version_id=_CORPUS_ID,
        dataset_key=_DATASET_KEY,
        version_label=_VERSION_LABEL,
        manifest=manifest,
        attractions=tuple(
            module.ImportAttraction(
                registry_key=registry_key,
                metadata=metadata,
                sections=sections,
            )
            for registry_key, metadata, sections, _ in entries
        ),
    )
    chunks_by_attraction = {
        metadata.attraction_id: chunks
        for _, metadata, _, chunks in entries
    }
    return request, chunks_by_attraction


def _two_attraction_entries():
    metadata_a = _metadata(_CANDIDATE_ID)
    section_a = _section(_CANDIDATE_ID)
    chunk_a = _chunk(_CANDIDATE_ID, section=section_a)
    metadata_b = _metadata(_OTHER_ID)
    section_b = _section(
        _OTHER_ID,
        chunk_type=ChunkType.highlights,
        content="鼓浪屿保留了丰富的历史建筑。",
    )
    chunk_b = _chunk(_OTHER_ID, section=section_b)
    return (
        ("xiamen:gulangyu", metadata_a, (section_a,), (chunk_a,)),
        ("xiamen:gulangyu:extra", metadata_b, (section_b,), (chunk_b,)),
    )


def _make_task4_importer(
    *,
    repository: FakeRepository,
    chunks_by_attraction: dict[UUID, tuple[SemanticChunk, ...]],
):
    module = _importer_module()
    identity_source = FakeIdentitySource(
        resolutions={
            "xiamen:gulangyu": _CANDIDATE_ID,
            "xiamen:gulangyu:extra": _OTHER_ID,
        }
    )
    return module.RagV2Importer(
        identity_source=identity_source,
        repository=repository,
        chunker=FakeChunker(
            chunks=(),
            chunks_by_attraction=chunks_by_attraction,
        ),
        passage_embedder=FakePassageEmbedder(),
    )


def test_task4_preserves_matching_rows_and_inserts_only_missing_rows() -> None:
    entries = _two_attraction_entries()
    request, chunks_by_attraction = _task4_request(entries)
    authoritative_corpus_id = _ALLOCATED_ID
    metadata_a, metadata_b = entries[0][1], entries[1][1]
    chunk_a, chunk_b = entries[0][3][0], entries[1][3][0]
    repository = FakeRepository(
        corpus=_corpus(
            "staging",
            corpus_version_id=authoritative_corpus_id,
            manifest_hash_value=manifest_hash(request.manifest),
        ),
        attraction_versions=(
            _version_record(metadata_a, corpus_version_id=authoritative_corpus_id),
        ),
        chunk_rows=(_chunk_row(chunk_a, corpus_version_id=authoritative_corpus_id),),
    )

    result = _make_task4_importer(
        repository=repository,
        chunks_by_attraction=chunks_by_attraction,
    ).import_corpus(request)

    assert repository.inserted_attraction_versions == [
        (_version_record(metadata_b, corpus_version_id=authoritative_corpus_id),)
    ]
    assert repository.inserted_chunks == [
        (ChunkInsert(corpus_version_id=authoritative_corpus_id, chunk=chunk_b),)
    ]
    assert repository.inserted_chunk_rows == [
        (_chunk_row(chunk_b, corpus_version_id=authoritative_corpus_id),)
    ]
    assert result.corpus.status == "staging"
    assert result.ready_for_activation is False
    assert repository.forbidden_calls == []


def test_task4_marks_attraction_version_immutable_conflict_and_does_not_replace_it() -> None:
    entries = _two_attraction_entries()[:1]
    request, chunks_by_attraction = _task4_request(entries)
    metadata = entries[0][1]
    conflicting_metadata = metadata.model_copy(update={"category": "different"})
    repository = FakeRepository(
        corpus=_corpus(
            "staging",
            manifest_hash_value=manifest_hash(request.manifest),
        ),
        attraction_versions=(_version_record(conflicting_metadata),),
    )

    with pytest.raises(AppError) as raised:
        _make_task4_importer(
            repository=repository,
            chunks_by_attraction=chunks_by_attraction,
        ).import_corpus(request)

    assert raised.value.code == "RAG_V2_VERSION_CONFLICT"
    assert raised.value.message == "RAG V2 corpus version conflicts with existing data"
    assert repository.inserted_attraction_versions == []
    assert repository.mark_failed_calls == [_CORPUS_ID]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("attraction_id", _OTHER_ID),
        ("chunk_type", ChunkType.highlights),
        ("ordinal", 1),
        ("content", "不同的规范化内容"),
        ("content_hash", "a" * 64),
        ("embedding_input_hash", "b" * 64),
        ("embedding_input_schema_version", "rag-v2-embedding-input-v2"),
        ("source_label", "另一来源"),
        ("source_url", "https://other.example/guide"),
        ("source_type", "secondary"),
        ("reviewed_on", date(2026, 2, 3)),
        ("embedding_model", "jina-embeddings-v4"),
        ("embedding_task", "retrieval.query"),
        ("embedding_dimensions", 1536),
    ],
)
def test_task4_marks_chunk_immutable_conflict_and_does_not_replace_it(
    field: str,
    value: object,
) -> None:
    entries = _two_attraction_entries()[:1]
    request, chunks_by_attraction = _task4_request(entries)
    chunk = entries[0][3][0]
    conflicting_row = replace(_chunk_row(chunk), **{field: value})
    repository = FakeRepository(
        corpus=_corpus(
            "staging",
            manifest_hash_value=manifest_hash(request.manifest),
        ),
        attraction_versions=(_version_record(entries[0][1]),),
        chunk_rows=(conflicting_row,),
    )

    with pytest.raises(AppError) as raised:
        _make_task4_importer(
            repository=repository,
            chunks_by_attraction=chunks_by_attraction,
        ).import_corpus(request)

    assert raised.value.code == "RAG_V2_VERSION_CONFLICT"
    assert raised.value.message == "RAG V2 corpus version conflicts with existing data"
    assert repository.inserted_chunks == []
    assert repository.mark_failed_calls == [_CORPUS_ID]


def test_task4_reads_both_authoritative_snapshots_before_any_insert() -> None:
    entries = _two_attraction_entries()
    request, chunks_by_attraction = _task4_request(entries)
    repository = FakeRepository(
        corpus=_corpus(
            "staging",
            manifest_hash_value=manifest_hash(request.manifest),
        )
    )

    _make_task4_importer(
        repository=repository,
        chunks_by_attraction=chunks_by_attraction,
    ).import_corpus(request)

    read_positions = [
        index
        for index, operation in enumerate(repository.operation_log)
        if operation in {"list_attraction_versions", "list_chunk_rows"}
    ]
    insert_positions = [
        index
        for index, operation in enumerate(repository.operation_log)
        if operation in {"insert_attraction_versions", "insert_chunks"}
    ]
    assert read_positions
    assert insert_positions
    assert max(read_positions) < min(insert_positions)


def test_task4_partial_rerun_keeps_matching_rows_and_is_not_ready() -> None:
    entries = _two_attraction_entries()
    request, chunks_by_attraction = _task4_request(entries)
    metadata_a, metadata_b = entries[0][1], entries[1][1]
    chunk_a, chunk_b = entries[0][3][0], entries[1][3][0]
    repository = FakeRepository(
        corpus=_corpus(
            "staging",
            manifest_hash_value=manifest_hash(request.manifest),
        ),
        attraction_versions=(_version_record(metadata_a),),
        chunk_rows=(_chunk_row(chunk_a),),
    )

    result = _make_task4_importer(
        repository=repository,
        chunks_by_attraction=chunks_by_attraction,
    ).import_corpus(request)

    assert repository.inserted_attraction_versions == [(_version_record(metadata_b),)]
    assert repository.inserted_chunks == [
        (ChunkInsert(corpus_version_id=_CORPUS_ID, chunk=chunk_b),)
    ]
    assert repository.attraction_versions == (_version_record(metadata_a),)
    assert repository.chunk_rows == (_chunk_row(chunk_a),)
    assert result.ready_for_activation is False
    assert repository.forbidden_calls == []


# Regression coverage: Task 3 already provides this observable staging no-op.
def test_task4_complete_staging_rerun_is_idempotent_and_does_not_claim_readiness() -> None:
    entries = _two_attraction_entries()
    request, chunks_by_attraction = _task4_request(entries)
    existing_versions = tuple(
        _version_record(metadata) for _, metadata, _, _ in entries
    )
    existing_rows = tuple(
        _chunk_row(chunk)
        for _, _, _, chunks in entries
        for chunk in chunks
    )
    repository = FakeRepository(
        corpus=_corpus(
            "staging",
            manifest_hash_value=manifest_hash(request.manifest),
        ),
        attraction_versions=existing_versions,
        chunk_rows=existing_rows,
    )

    result = _make_task4_importer(
        repository=repository,
        chunks_by_attraction=chunks_by_attraction,
    ).import_corpus(request)

    assert repository.inserted_attraction_versions == []
    assert repository.inserted_chunks == []
    assert result.corpus.status == "staging"
    assert result.chunk_rows == existing_rows
    assert result.ready_for_activation is False
    assert repository.forbidden_calls == []


def test_task4_result_chunk_order_is_independent_of_caller_order() -> None:
    entries = _two_attraction_entries()
    canonical_request, canonical_chunks = _task4_request(entries)
    reversed_request, reversed_chunks = _task4_request(tuple(reversed(entries)))

    canonical_result = _make_task4_importer(
        repository=FakeRepository(
            corpus=_corpus(
                "staging",
                manifest_hash_value=manifest_hash(canonical_request.manifest),
            )
        ),
        chunks_by_attraction=canonical_chunks,
    ).import_corpus(canonical_request)
    reversed_result = _make_task4_importer(
        repository=FakeRepository(
            corpus=_corpus(
                "staging",
                manifest_hash_value=manifest_hash(reversed_request.manifest),
            )
        ),
        chunks_by_attraction=reversed_chunks,
    ).import_corpus(reversed_request)

    def row_order(rows: tuple[ChunkRow, ...]) -> tuple[tuple[object, ...], ...]:
        return tuple(
            (
                str(row.attraction_id),
                row.chunk_type.value,
                row.ordinal,
                row.chunk_key,
            )
            for row in rows
        )

    expected_order = tuple(sorted(row_order(canonical_result.chunk_rows)))
    assert row_order(canonical_result.chunk_rows) == expected_order
    assert row_order(reversed_result.chunk_rows) == expected_order
    assert row_order(reversed_result.chunk_rows) == row_order(
        canonical_result.chunk_rows
    )
