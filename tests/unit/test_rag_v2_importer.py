from __future__ import annotations

from dataclasses import FrozenInstanceError, fields, replace
from datetime import date, datetime, timezone
from importlib import import_module
from uuid import UUID

import pytest

from app.core.errors import AppError
from app.rag_v2.chunking import chunk_key_for
from app.rag_v2.incremental import EmbeddingIdentity, PreviousEmbedding
from app.rag_v2.hashing import (
    build_embedding_input,
    canonical_embedding_text,
    content_hash,
    embedding_input_hash,
    manifest_hash,
    metadata_hash,
    normalize_content,
)
from app.rag_v2.models import (
    AttractionLifecycleStatus,
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
    StableAttraction,
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
        stable_attractions: tuple[StableAttraction, ...] = (),
        reuse_embeddings: tuple[PreviousEmbedding, ...] = (),
        final_corpus: CorpusVersion | None = None,
        final_attraction_versions: tuple[AttractionVersionRecord, ...] | None = None,
        final_chunk_rows: tuple[ChunkRow, ...] | None = None,
        create_error: AppError | None = None,
        snapshot_error: AppError | None = None,
        reuse_error: AppError | None = None,
        embedding_failure_error: AppError | None = None,
        allow_task5_operations: bool = False,
    ) -> None:
        self.corpus = corpus
        self.attraction_versions = attraction_versions
        self.chunk_rows = chunk_rows
        self.stable_attractions = list(stable_attractions)
        self.reuse_embeddings = reuse_embeddings
        self.final_corpus = final_corpus
        self.final_attraction_versions = final_attraction_versions
        self.final_chunk_rows = final_chunk_rows
        self.create_error = create_error
        self.snapshot_error = snapshot_error
        self.reuse_error = reuse_error
        self.embedding_failure_error = embedding_failure_error
        self.allow_task5_operations = allow_task5_operations
        self.create_calls: list[dict[str, object]] = []
        self.attraction_version_calls: list[UUID] = []
        self.chunk_row_calls: list[UUID] = []
        self.inserted_attraction_versions: list[
            tuple[AttractionVersionRecord, ...]
        ] = []
        self.get_attraction_calls: list[UUID] = []
        self.inserted_attractions: list[StableAttraction] = []
        self.inserted_chunks: list[tuple[ChunkInsert, ...]] = []
        self.inserted_chunk_rows: list[tuple[ChunkRow, ...]] = []
        self.mark_failed_calls: list[UUID] = []
        self.operation_log: list[str] = []
        self.forbidden_calls: list[str] = []
        self.reuse_calls: list[dict[str, object]] = []
        self.embedded_calls: list[dict[str, object]] = []
        self.failed_embedding_calls: list[dict[str, object]] = []
        self.retry_reset_calls: list[dict[str, object]] = []
        self.corpus_reads: list[UUID] = []
        self._attraction_version_read_count = 0
        self._chunk_row_read_count = 0

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
        self._attraction_version_read_count += 1
        if (
            self._attraction_version_read_count > 1
            and self.final_attraction_versions is not None
        ):
            return self.final_attraction_versions
        return self.attraction_versions

    def list_chunk_rows(self, *, corpus_version_id: UUID) -> tuple[ChunkRow, ...]:
        self.operation_log.append("list_chunk_rows")
        self.chunk_row_calls.append(corpus_version_id)
        if self.snapshot_error is not None:
            raise self.snapshot_error
        self._chunk_row_read_count += 1
        if self._chunk_row_read_count > 1 and self.final_chunk_rows is not None:
            return self.final_chunk_rows
        return self.chunk_rows

    def get_corpus_version(self, *, corpus_version_id: UUID) -> CorpusVersion | None:
        self._require_task5_operation("get_corpus_version")
        self.operation_log.append("get_corpus_version")
        self.corpus_reads.append(corpus_version_id)
        return self.final_corpus if self.final_corpus is not None else self.corpus

    def get_attraction(self, *, attraction_id: UUID) -> StableAttraction | None:
        self.operation_log.append("get_attraction")
        self.get_attraction_calls.append(attraction_id)
        return next(
            (
                attraction
                for attraction in self.stable_attractions
                if attraction.attraction_id == attraction_id
            ),
            None,
        )

    def insert_attraction(self, attraction: StableAttraction) -> StableAttraction:
        self.operation_log.append("insert_attraction")
        self.inserted_attractions.append(attraction)
        self.stable_attractions.append(attraction)
        return attraction

    def list_embedded_chunks_for_reuse(
        self,
        *,
        dataset_key: str,
        embedding_input_hash: str,
        exclude_corpus_version_id: UUID,
    ) -> tuple[PreviousEmbedding, ...]:
        self._require_task5_operation("list_embedded_chunks_for_reuse")
        self.operation_log.append("list_embedded_chunks_for_reuse")
        self.reuse_calls.append(
            {
                "dataset_key": dataset_key,
                "embedding_input_hash": embedding_input_hash,
                "exclude_corpus_version_id": exclude_corpus_version_id,
            }
        )
        if self.reuse_error is not None:
            raise self.reuse_error
        return self.reuse_embeddings

    def mark_chunk_embedded(
        self,
        *,
        corpus_version_id: UUID,
        chunk_key: str,
        embedding: tuple[float, ...],
    ) -> ChunkRow:
        self._require_task5_operation("mark_chunk_embedded")
        self.operation_log.append("mark_chunk_embedded")
        self.embedded_calls.append(
            {
                "corpus_version_id": corpus_version_id,
                "chunk_key": chunk_key,
                "embedding": embedding,
            }
        )
        return next(row for row in self.chunk_rows if row.chunk_key == chunk_key)

    def mark_chunk_embedding_failed(
        self,
        *,
        corpus_version_id: UUID,
        chunk_key: str,
        error_code: str,
        error_message: str | None = None,
    ) -> ChunkRow:
        self._require_task5_operation("mark_chunk_embedding_failed")
        self.operation_log.append("mark_chunk_embedding_failed")
        self.failed_embedding_calls.append(
            {
                "corpus_version_id": corpus_version_id,
                "chunk_key": chunk_key,
                "error_code": error_code,
                "error_message": error_message,
            }
        )
        if self.embedding_failure_error is not None:
            raise self.embedding_failure_error
        return next(row for row in self.chunk_rows if row.chunk_key == chunk_key)

    def reset_chunk_embedding_for_retry(
        self,
        *,
        corpus_version_id: UUID,
        chunk_key: str,
    ) -> ChunkRow:
        self._require_task5_operation("reset_chunk_embedding_for_retry")
        self.operation_log.append("reset_chunk_embedding_for_retry")
        self.retry_reset_calls.append(
            {
                "corpus_version_id": corpus_version_id,
                "chunk_key": chunk_key,
            }
        )
        return next(row for row in self.chunk_rows if row.chunk_key == chunk_key)

    def _require_task5_operation(self, name: str) -> None:
        if not self.allow_task5_operations:
            self.forbidden_calls.append(name)
            raise AssertionError(f"Task 4 must not call {name}")

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
        self.chunk_rows += rows
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
    passage_embedder: object | None = None,
):
    module = _importer_module()
    return module.RagV2Importer(
        identity_source=identity_source or FakeIdentitySource(
            resolutions={"xiamen:gulangyu": _CANDIDATE_ID}
        ),
        repository=repository,
        chunker=chunker or FakeChunker(chunks=(_chunk(),)),
        passage_embedder=passage_embedder or FakePassageEmbedder(),
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


def test_import_persists_missing_stable_attraction_before_version_child() -> None:
    class _StopAfterVersionInsert(Exception):
        pass

    class StableParentRepository(FakeRepository):
        def __init__(self) -> None:
            super().__init__(corpus=_corpus("staging"))
            self.get_attraction_calls: list[UUID] = []
            self.inserted_attractions: list[StableAttraction] = []

        def get_attraction(self, *, attraction_id: UUID) -> None:
            self.operation_log.append("get_attraction")
            self.get_attraction_calls.append(attraction_id)
            return None

        def insert_attraction(self, attraction: StableAttraction) -> StableAttraction:
            self.operation_log.append("insert_attraction")
            self.inserted_attractions.append(attraction)
            return attraction

        def insert_attraction_versions(
            self,
            records: tuple[AttractionVersionRecord, ...],
        ) -> tuple[AttractionVersionRecord, ...]:
            super().insert_attraction_versions(records)
            raise _StopAfterVersionInsert

    repository = StableParentRepository()

    with pytest.raises(_StopAfterVersionInsert):
        _make_importer(repository=repository).import_corpus(_request())

    assert repository.get_attraction_calls == [_CANDIDATE_ID]
    assert len(repository.inserted_attractions) == 1
    stable = repository.inserted_attractions[0]
    assert isinstance(stable, StableAttraction)
    assert stable.attraction_id == _CANDIDATE_ID
    assert stable.lifecycle_status is AttractionLifecycleStatus.active
    assert stable.retired_at is None
    assert stable.merged_into_attraction_id is None
    assert stable.created_at.tzinfo is not None
    assert stable.created_at.utcoffset() is not None
    assert repository.operation_log.index("get_attraction") < repository.operation_log.index(
        "insert_attraction"
    ) < repository.operation_log.index("insert_attraction_versions")


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
    vector = (0.25,) * 1024
    repository = FakeRepository(
        corpus=authoritative_corpus,
        attraction_versions=(version,),
        chunk_rows=(row,),
        reuse_embeddings=(_previous_embedding(chunk, vector=vector),),
        final_attraction_versions=(version,),
        final_chunk_rows=(
            replace(_embedded_row(chunk, vector), corpus_version_id=_OTHER_ID),
        ),
        allow_task5_operations=True,
    )
    importer = _make_importer(
        repository=repository,
        chunker=FakeChunker(chunks=(chunk,)),
        passage_embedder=FakeTask5PassageEmbedder(vector=vector),
    )

    result = importer.import_corpus(request)

    assert result.corpus is authoritative_corpus
    assert result.corpus.corpus_version_id == _OTHER_ID
    assert repository.create_calls == [
        {
            "corpus_version_id": _CORPUS_ID,
            "dataset_key": _DATASET_KEY,
            "version_label": _VERSION_LABEL,
            "manifest_hash": manifest_hash(request.manifest),
        }
    ]
    assert repository.attraction_version_calls[0] == _OTHER_ID
    assert repository.chunk_row_calls[0] == _OTHER_ID


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
    allocated_metadata = _metadata(_ALLOCATED_ID)
    vector = (0.25,) * 1024
    repository = FakeRepository(
        corpus=_corpus(
            "staging",
            manifest_hash_value=manifest_hash(request.manifest),
        ),
        attraction_versions=(_version_record(allocated_metadata),),
        chunk_rows=(_chunk_row(allocated_chunk),),
        reuse_embeddings=(_previous_embedding(allocated_chunk, vector=vector),),
        final_attraction_versions=(_version_record(allocated_metadata),),
        final_chunk_rows=(_embedded_row(allocated_chunk, vector),),
        allow_task5_operations=True,
    )
    chunker = FakeChunker(chunks=(allocated_chunk,))

    _make_importer(
        repository=repository,
        identity_source=identity,
        chunker=chunker,
        passage_embedder=FakeTask5PassageEmbedder(vector=vector),
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
    passage_embedder: object | None = None,
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
        passage_embedder=passage_embedder or FakePassageEmbedder(),
    )


def test_task4_preserves_matching_rows_and_inserts_only_missing_rows() -> None:
    entries = _two_attraction_entries()
    request, chunks_by_attraction = _task4_request(entries)
    authoritative_corpus_id = _ALLOCATED_ID
    metadata_a, metadata_b = entries[0][1], entries[1][1]
    chunk_a, chunk_b = entries[0][3][0], entries[1][3][0]
    vector = (0.25,) * 1024
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
        reuse_embeddings=(_previous_embedding(chunk_a, vector=vector),),
        final_attraction_versions=(
            _version_record(metadata_a, corpus_version_id=authoritative_corpus_id),
            _version_record(metadata_b, corpus_version_id=authoritative_corpus_id),
        ),
        final_chunk_rows=(
            replace(_embedded_row(chunk_a, vector), corpus_version_id=authoritative_corpus_id),
            replace(_embedded_row(chunk_b, vector), corpus_version_id=authoritative_corpus_id),
        ),
        allow_task5_operations=True,
    )

    result = _make_task4_importer(
        repository=repository,
        chunks_by_attraction=chunks_by_attraction,
        passage_embedder=FakeTask5PassageEmbedder(vector=vector),
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
    metadata_a, metadata_b = entries[0][1], entries[1][1]
    chunk_a, chunk_b = entries[0][3][0], entries[1][3][0]
    vector = (0.25,) * 1024
    repository = FakeRepository(
        corpus=_corpus(
            "staging",
            manifest_hash_value=manifest_hash(request.manifest),
        ),
        final_attraction_versions=(
            _version_record(metadata_a),
            _version_record(metadata_b),
        ),
        final_chunk_rows=(
            _embedded_row(chunk_a, vector),
            _embedded_row(chunk_b, vector),
        ),
        allow_task5_operations=True,
    )

    _make_task4_importer(
        repository=repository,
        chunks_by_attraction=chunks_by_attraction,
        passage_embedder=FakeTask5PassageEmbedder(vector=vector),
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
    assert max(read_positions[:2]) < min(insert_positions)


def test_task4_partial_rerun_keeps_matching_rows_and_inserts_only_missing_rows() -> None:
    entries = _two_attraction_entries()
    request, chunks_by_attraction = _task4_request(entries)
    metadata_a, metadata_b = entries[0][1], entries[1][1]
    chunk_a, chunk_b = entries[0][3][0], entries[1][3][0]
    vector = (0.25,) * 1024
    repository = FakeRepository(
        corpus=_corpus(
            "staging",
            manifest_hash_value=manifest_hash(request.manifest),
        ),
        attraction_versions=(_version_record(metadata_a),),
        chunk_rows=(_chunk_row(chunk_a),),
        reuse_embeddings=(_previous_embedding(chunk_a, vector=vector),),
        final_attraction_versions=(
            _version_record(metadata_a),
            _version_record(metadata_b),
        ),
        final_chunk_rows=(
            _embedded_row(chunk_a, vector),
            _embedded_row(chunk_b, vector),
        ),
        allow_task5_operations=True,
    )

    result = _make_task4_importer(
        repository=repository,
        chunks_by_attraction=chunks_by_attraction,
        passage_embedder=FakeTask5PassageEmbedder(vector=vector),
    ).import_corpus(request)

    assert repository.inserted_attraction_versions == [(_version_record(metadata_b),)]
    assert repository.inserted_chunks == [
        (ChunkInsert(corpus_version_id=_CORPUS_ID, chunk=chunk_b),)
    ]
    assert repository.attraction_versions == (_version_record(metadata_a),)
    assert repository.chunk_rows == (
        _chunk_row(chunk_a),
        _chunk_row(chunk_b),
    )
    assert repository.forbidden_calls == []


def test_task4_complete_staging_rerun_is_idempotent_and_preserves_embedded_rows() -> None:
    entries = _two_attraction_entries()
    request, chunks_by_attraction = _task4_request(entries)
    existing_versions = tuple(
        _version_record(metadata) for _, metadata, _, _ in entries
    )
    vector = (0.25,) * 1024
    existing_rows = tuple(
        _embedded_row(chunk, vector)
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
        allow_task5_operations=True,
    )
    embedder = FakeTask5PassageEmbedder(vector=vector)

    result = _make_task4_importer(
        repository=repository,
        chunks_by_attraction=chunks_by_attraction,
        passage_embedder=embedder,
    ).import_corpus(request)

    assert repository.inserted_attraction_versions == []
    assert repository.inserted_chunks == []
    assert result.corpus.status == "staging"
    assert result.chunk_rows == existing_rows
    assert result.ready_for_activation is True
    assert embedder.calls == []
    assert repository.forbidden_calls == []


def test_task4_result_chunk_order_is_independent_of_caller_order() -> None:
    entries = _two_attraction_entries()
    canonical_request, canonical_chunks = _task4_request(entries)
    reversed_request, reversed_chunks = _task4_request(tuple(reversed(entries)))
    metadata_a, metadata_b = entries[0][1], entries[1][1]
    chunk_a, chunk_b = entries[0][3][0], entries[1][3][0]
    vector = (0.25,) * 1024

    canonical_result = _make_task4_importer(
        repository=FakeRepository(
            corpus=_corpus(
                "staging",
                manifest_hash_value=manifest_hash(canonical_request.manifest),
            ),
            reuse_embeddings=(_previous_embedding(chunk_a, vector=vector),),
            final_attraction_versions=(
                _version_record(metadata_a),
                _version_record(metadata_b),
            ),
            final_chunk_rows=(
                _embedded_row(chunk_a, vector),
                _embedded_row(chunk_b, vector),
            ),
            allow_task5_operations=True,
        ),
        chunks_by_attraction=canonical_chunks,
        passage_embedder=FakeTask5PassageEmbedder(vector=vector),
    ).import_corpus(canonical_request)
    reversed_result = _make_task4_importer(
        repository=FakeRepository(
            corpus=_corpus(
                "staging",
                manifest_hash_value=manifest_hash(reversed_request.manifest),
            ),
            reuse_embeddings=(_previous_embedding(chunk_a, vector=vector),),
            final_attraction_versions=(
                _version_record(metadata_a),
                _version_record(metadata_b),
            ),
            final_chunk_rows=(
                _embedded_row(chunk_a, vector),
                _embedded_row(chunk_b, vector),
            ),
            allow_task5_operations=True,
        ),
        chunks_by_attraction=reversed_chunks,
        passage_embedder=FakeTask5PassageEmbedder(vector=vector),
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


class FakeTask5PassageEmbedder:
    def __init__(
        self,
        *,
        vector: tuple[float, ...] | None = None,
        error: AppError | None = None,
    ) -> None:
        self.vector = vector or (0.25,) * 1024
        self.error = error
        self.calls: list[str] = []

    def embed_passage(self, text: str) -> tuple[float, ...]:
        self.calls.append(text)
        if self.error is not None:
            raise self.error
        return self.vector


def _embedded_row(chunk: SemanticChunk, vector: tuple[float, ...]) -> ChunkRow:
    return replace(
        _chunk_row(chunk),
        embedding=vector,
        status=ChunkStatus.embedded,
    )


def _previous_embedding(
    chunk: SemanticChunk,
    *,
    vector: tuple[float, ...],
    **identity_updates: object,
) -> PreviousEmbedding:
    values: dict[str, object] = {
        "embedding_input_hash": chunk.embedding_input_hash,
        "embedding_model": "jina-embeddings-v3",
        "embedding_task": "retrieval.passage",
        "embedding_dimensions": 1024,
        "embedding_input_schema_version": "rag-v2-embedding-input-v1",
    }
    values.update(identity_updates)
    return PreviousEmbedding(
        chunk_key="historical-chunk",
        identity=EmbeddingIdentity(**values),
        vector=vector,
        validated_corpus=True,
    )


def _make_task5_importer(
    *,
    repository: FakeRepository,
    passage_embedder: FakeTask5PassageEmbedder,
    chunk: SemanticChunk | None = None,
):
    module = _importer_module()
    return module.RagV2Importer(
        identity_source=FakeIdentitySource(
            resolutions={"xiamen:gulangyu": _CANDIDATE_ID}
        ),
        repository=repository,
        chunker=FakeChunker(chunks=(chunk or _chunk(),)),
        passage_embedder=passage_embedder,
    )


def _task5_staging_repository(
    *,
    request,
    chunk: SemanticChunk,
    initial_row: ChunkRow | None = None,
    final_corpus: CorpusVersion | None = None,
    final_versions: tuple[AttractionVersionRecord, ...] | None = None,
    final_rows: tuple[ChunkRow, ...] | None = None,
    reuse_embeddings: tuple[PreviousEmbedding, ...] = (),
    reuse_error: AppError | None = None,
    embedding_failure_error: AppError | None = None,
) -> FakeRepository:
    metadata = request.attractions[0].metadata
    return FakeRepository(
        corpus=_corpus(
            "staging",
            manifest_hash_value=manifest_hash(request.manifest),
        ),
        attraction_versions=(_version_record(metadata),),
        chunk_rows=(initial_row if initial_row is not None else _chunk_row(chunk),),
        reuse_embeddings=reuse_embeddings,
        final_corpus=final_corpus,
        final_attraction_versions=(
            (_version_record(metadata),)
            if final_versions is None
            else final_versions
        ),
        final_chunk_rows=final_rows,
        reuse_error=reuse_error,
        embedding_failure_error=embedding_failure_error,
        allow_task5_operations=True,
    )


def test_task5_reuses_exact_historical_vector_and_reads_fresh_authoritative_state() -> None:
    request = _request()
    chunk = _chunk()
    vector = (0.125,) * 1024
    repository = _task5_staging_repository(
        request=request,
        chunk=chunk,
        reuse_embeddings=(_previous_embedding(chunk, vector=vector),),
        final_rows=(_embedded_row(chunk, vector),),
    )
    embedder = FakeTask5PassageEmbedder()

    result = _make_task5_importer(
        repository=repository,
        passage_embedder=embedder,
    ).import_corpus(request)

    assert repository.reuse_calls == [
        {
            "dataset_key": _DATASET_KEY,
            "embedding_input_hash": chunk.embedding_input_hash,
            "exclude_corpus_version_id": _CORPUS_ID,
        }
    ]
    assert repository.embedded_calls == [
        {
            "corpus_version_id": _CORPUS_ID,
            "chunk_key": chunk.chunk_key,
            "embedding": vector,
        }
    ]
    assert embedder.calls == []
    assert repository.corpus_reads == [_CORPUS_ID]
    assert repository.attraction_version_calls == [_CORPUS_ID, _CORPUS_ID]
    assert repository.chunk_row_calls == [_CORPUS_ID, _CORPUS_ID]
    assert result.chunk_rows == (_embedded_row(chunk, vector),)
    assert result.ready_for_activation is True
    assert result.reused_chunk_keys == (chunk.chunk_key,)
    assert result.embedded_chunk_keys == ()
    assert repository.forbidden_calls == []


def test_task5_reuses_when_only_provenance_changes() -> None:
    section = _section(source_label="更新后的来源", source_url="https://example.com/new")
    chunk = _chunk(section=section)
    request = _request(metadata=_metadata(), section=section)
    vector = (0.375,) * 1024
    repository = _task5_staging_repository(
        request=request,
        chunk=chunk,
        reuse_embeddings=(_previous_embedding(chunk, vector=vector),),
        final_rows=(_embedded_row(chunk, vector),),
    )
    embedder = FakeTask5PassageEmbedder()

    _make_task5_importer(
        repository=repository,
        passage_embedder=embedder,
        chunk=chunk,
    ).import_corpus(request)

    assert embedder.calls == []
    assert repository.embedded_calls == [
        {
            "corpus_version_id": _CORPUS_ID,
            "chunk_key": chunk.chunk_key,
            "embedding": vector,
        }
    ]


def test_task5_reembeds_when_canonical_embedding_input_changes() -> None:
    section = _section(content="鼓浪屿保留了丰富的历史建筑与海岛景观。")
    chunk = _chunk(section=section)
    request = _request(section=section)
    vector = (0.625,) * 1024
    repository = _task5_staging_repository(
        request=request,
        chunk=chunk,
        reuse_embeddings=(),
        final_rows=(_embedded_row(chunk, vector),),
    )
    embedder = FakeTask5PassageEmbedder(vector=vector)

    _make_task5_importer(
        repository=repository,
        passage_embedder=embedder,
        chunk=chunk,
    ).import_corpus(request)

    assert embedder.calls == [
        canonical_embedding_text(
            build_embedding_input(
                canonical_attraction_name=_metadata().canonical_name,
                destination_name=_metadata().destination.destination_name,
                destination_code=_metadata().destination.destination_code,
                destination_level=_metadata().destination.destination_level,
                chunk_type=chunk.chunk_type,
                normalized_content=chunk.normalized_content,
            )
        )
    ]
    assert repository.embedded_calls[0]["embedding"] == vector


def test_task5_records_provider_failure_without_failing_the_corpus() -> None:
    request = _request()
    chunk = _chunk()
    original_error = AppError(
        "RAG_V2_EMBEDDING_UNAVAILABLE",
        "RAG V2 embedding is unavailable",
    )
    repository = _task5_staging_repository(
        request=request,
        chunk=chunk,
        embedding_failure_error=AppError(
            "RAG_V2_UNAVAILABLE",
            "RAG V2 persistence is unavailable",
        ),
    )
    embedder = FakeTask5PassageEmbedder(error=original_error)

    with pytest.raises(AppError) as raised:
        _make_task5_importer(
            repository=repository,
            passage_embedder=embedder,
            chunk=chunk,
        ).import_corpus(request)

    assert raised.value is original_error
    assert repository.failed_embedding_calls == [
        {
            "corpus_version_id": _CORPUS_ID,
            "chunk_key": chunk.chunk_key,
            "error_code": "RAG_V2_EMBEDDING_UNAVAILABLE",
            "error_message": "RAG V2 embedding is unavailable",
        }
    ]
    assert repository.mark_failed_calls == []


def test_task5_preserves_ordinary_repository_app_errors_without_failure_mutation() -> None:
    request = _request()
    chunk = _chunk()
    original_error = AppError(
        "RAG_V2_UNAVAILABLE",
        "RAG V2 persistence is unavailable",
    )
    repository = _task5_staging_repository(
        request=request,
        chunk=chunk,
        reuse_error=original_error,
    )

    with pytest.raises(AppError) as raised:
        _make_task5_importer(
            repository=repository,
            passage_embedder=FakeTask5PassageEmbedder(),
        ).import_corpus(request)

    assert raised.value is original_error
    assert repository.mark_failed_calls == []


def test_task5_failed_chunk_requires_explicit_retry() -> None:
    request = _request()
    chunk = _chunk()
    failed_row = replace(
        _chunk_row(chunk),
        status=ChunkStatus.failed,
        embedding_error_code="RAG_V2_EMBEDDING_UNAVAILABLE",
    )
    repository = _task5_staging_repository(
        request=request,
        chunk=chunk,
        initial_row=failed_row,
    )

    with pytest.raises(AppError) as raised:
        _make_task5_importer(
            repository=repository,
            passage_embedder=FakeTask5PassageEmbedder(),
        ).import_corpus(request)

    assert raised.value.code == "RAG_V2_INVALID_LIFECYCLE"
    assert repository.retry_reset_calls == []
    assert repository.mark_failed_calls == []


def test_task5_retry_resets_failed_chunk_before_embedding() -> None:
    chunk = _chunk()
    request = _request(retry_failed=True)
    vector = (0.75,) * 1024
    failed_row = replace(
        _chunk_row(chunk),
        status=ChunkStatus.failed,
        embedding_error_code="RAG_V2_EMBEDDING_UNAVAILABLE",
    )
    repository = _task5_staging_repository(
        request=request,
        chunk=chunk,
        initial_row=failed_row,
        final_rows=(_embedded_row(chunk, vector),),
    )

    _make_task5_importer(
        repository=repository,
        passage_embedder=FakeTask5PassageEmbedder(vector=vector),
    ).import_corpus(request)

    assert repository.retry_reset_calls == [
        {"corpus_version_id": _CORPUS_ID, "chunk_key": chunk.chunk_key}
    ]
    assert repository.embedded_calls[0]["embedding"] == vector


@pytest.mark.parametrize(
    ("final_versions", "final_rows", "expected_code"),
    [
        ((), None, "RAG_V2_VERSION_CONFLICT"),
        (
            (_version_record(_metadata(_OTHER_ID)),),
            None,
            "RAG_V2_VERSION_CONFLICT",
        ),
        (None, (), "RAG_V2_VERSION_CONFLICT"),
        (
            None,
            (
                _embedded_row(_chunk(), (0.0,) * 1024),
                _embedded_row(
                    _chunk(
                        _OTHER_ID,
                        section=_section(
                            _OTHER_ID,
                            chunk_type=ChunkType.highlights,
                            content="额外片段。",
                        ),
                    ),
                    (0.0,) * 1024,
                ),
            ),
            "RAG_V2_VERSION_CONFLICT",
        ),
    ],
)
def test_task5_rejects_missing_or_extra_authoritative_rows(
    final_versions: tuple[AttractionVersionRecord, ...] | None,
    final_rows: tuple[ChunkRow, ...] | None,
    expected_code: str,
) -> None:
    request = _request()
    chunk = _chunk()
    vector = (0.625,) * 1024
    if final_rows is None:
        final_rows = (_embedded_row(chunk, vector),)
    repository = _task5_staging_repository(
        request=request,
        chunk=chunk,
        reuse_embeddings=(_previous_embedding(chunk, vector=vector),),
        final_versions=final_versions,
        final_rows=final_rows,
    )

    with pytest.raises(AppError) as raised:
        _make_task5_importer(
            repository=repository,
            passage_embedder=FakeTask5PassageEmbedder(),
        ).import_corpus(request)

    assert raised.value.code == expected_code


@pytest.mark.parametrize("status", [ChunkStatus.pending, ChunkStatus.failed])
def test_task5_pending_or_failed_authoritative_chunk_is_retryable_not_ready(
    status: ChunkStatus,
) -> None:
    request = _request()
    chunk = _chunk()
    row = replace(
        _chunk_row(chunk),
        status=status,
        embedding_error_code="retryable" if status is ChunkStatus.failed else None,
    )
    repository = _task5_staging_repository(
        request=request,
        chunk=chunk,
        initial_row=_embedded_row(chunk, (0.875,) * 1024),
        final_rows=(row,),
    )

    with pytest.raises(AppError) as raised:
        _make_task5_importer(
            repository=repository,
            passage_embedder=FakeTask5PassageEmbedder(),
        ).import_corpus(request)

    assert raised.value.code == "RAG_V2_INVALID_LIFECYCLE"
    assert repository.mark_failed_calls == []


def _task5_ready_state_repository(
    *,
    request,
    chunk: SemanticChunk,
    final_corpus: CorpusVersion | None = None,
    final_versions: tuple[AttractionVersionRecord, ...] | None = None,
    final_rows: tuple[ChunkRow, ...] | None = None,
) -> FakeRepository:
    vector = (0.875,) * 1024
    return _task5_staging_repository(
        request=request,
        chunk=chunk,
        reuse_embeddings=(_previous_embedding(chunk, vector=vector),),
        final_corpus=final_corpus,
        final_versions=final_versions,
        final_rows=final_rows or (_embedded_row(chunk, vector),),
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("status", "failed"),
        ("corpus_version_id", _OTHER_ID),
        ("dataset_key", "other-dataset"),
        ("version_label", "other-version"),
        ("manifest_hash", "f" * 64),
    ],
)
def test_task5_rejects_final_corpus_identity_or_manifest_mismatch(
    field: str,
    value: object,
) -> None:
    request = _request()
    chunk = _chunk()
    repository = _task5_ready_state_repository(request=request, chunk=chunk)
    repository.final_corpus = replace(repository.corpus, **{field: value})

    with pytest.raises(AppError) as raised:
        _make_task5_importer(
            repository=repository,
            passage_embedder=FakeTask5PassageEmbedder(),
        ).import_corpus(request)

    assert raised.value.code == "RAG_V2_VERSION_CONFLICT"
    assert repository.forbidden_calls == []


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("canonical_name", "不同名称"),
        ("aliases", ("别名",)),
        (
            "destination",
            _metadata().destination.model_copy(update={"destination_name": "福州市"}),
        ),
        ("category", "museum"),
        ("tags", ("不同标签",)),
        ("status", AttractionVersionStatus.suppressed),
    ],
)
def test_task5_rejects_final_attraction_metadata_or_hash_mismatch(
    field: str,
    value: object,
) -> None:
    request = _request()
    chunk = _chunk()
    expected = _version_record(_metadata())
    changed_metadata = expected.metadata.model_copy(update={field: value})
    repository = _task5_ready_state_repository(
        request=request,
        chunk=chunk,
        final_versions=(
            replace(expected, metadata=changed_metadata),
        ),
    )

    with pytest.raises(AppError) as raised:
        _make_task5_importer(
            repository=repository,
            passage_embedder=FakeTask5PassageEmbedder(),
        ).import_corpus(request)

    assert raised.value.code == "RAG_V2_VERSION_CONFLICT"


def test_task5_rejects_final_attraction_metadata_hash_mismatch() -> None:
    request = _request()
    chunk = _chunk()
    expected = _version_record(_metadata())
    repository = _task5_ready_state_repository(
        request=request,
        chunk=chunk,
        final_versions=(replace(expected, metadata_hash="f" * 64),),
    )

    with pytest.raises(AppError) as raised:
        _make_task5_importer(
            repository=repository,
            passage_embedder=FakeTask5PassageEmbedder(),
        ).import_corpus(request)

    assert raised.value.code == "RAG_V2_VERSION_CONFLICT"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("attraction_id", _OTHER_ID),
        ("chunk_key", "different-chunk-key"),
        ("chunk_type", ChunkType.highlights),
        ("ordinal", 1),
        ("content", "不同的规范化内容"),
        ("content_hash", "a" * 64),
        ("embedding_input_hash", "b" * 64),
        ("embedding_input_schema_version", "rag-v2-embedding-input-v2"),
        ("embedding_model", "jina-embeddings-v4"),
        ("embedding_task", "retrieval.query"),
        ("embedding_dimensions", 1536),
    ],
)
def test_task5_rejects_final_immutable_chunk_or_profile_mismatch(
    field: str,
    value: object,
) -> None:
    request = _request()
    chunk = _chunk()
    repository = _task5_ready_state_repository(
        request=request,
        chunk=chunk,
        final_rows=(replace(_embedded_row(chunk, (0.875,) * 1024), **{field: value}),),
    )

    with pytest.raises(AppError) as raised:
        _make_task5_importer(
            repository=repository,
            passage_embedder=FakeTask5PassageEmbedder(),
        ).import_corpus(request)

    assert raised.value.code == "RAG_V2_VERSION_CONFLICT"


def test_task5_requires_a_complete_finite_non_boolean_embedding() -> None:
    embedding = None
    request = _request()
    chunk = _chunk()
    repository = _task5_ready_state_repository(
        request=request,
        chunk=chunk,
        final_rows=(
            replace(
                _embedded_row(chunk, (0.875,) * 1024),
                embedding=embedding,
            ),
        ),
    )

    with pytest.raises(AppError) as raised:
        _make_task5_importer(
            repository=repository,
            passage_embedder=FakeTask5PassageEmbedder(),
        ).import_corpus(request)

    assert raised.value.code == "RAG_V2_INVALID_LIFECYCLE"
    assert repository.mark_failed_calls == []


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("source_label", "   "),
        ("source_type", "\t"),
        ("source_url", "not-a-url"),
        ("source_url", "http://example.com/source"),
        ("source_url", "https:///missing-host"),
        ("source_url", "https://user@example.com/source"),
        ("source_url", "https://:password@example.com/source"),
    ],
)
def test_task5_requires_complete_structural_provenance_in_final_rows(
    field: str,
    value: object,
) -> None:
    request = _request()
    chunk = _chunk()
    repository = _task5_ready_state_repository(
        request=request,
        chunk=chunk,
        final_rows=(
            replace(
                _embedded_row(chunk, (0.875,) * 1024),
                **{field: value},
            ),
        ),
    )

    with pytest.raises(AppError) as raised:
        _make_task5_importer(
            repository=repository,
            passage_embedder=FakeTask5PassageEmbedder(),
        ).import_corpus(request)

    assert raised.value.code == "RAG_V2_INVALID_LIFECYCLE"
    assert repository.mark_failed_calls == []


def test_task5_returns_fresh_chunk_rows_in_stable_identity_order() -> None:
    entries = _two_attraction_entries()
    request, chunks_by_attraction = _task4_request(entries)
    metadata_a, metadata_b = entries[0][1], entries[1][1]
    chunk_a, chunk_b = entries[0][3][0], entries[1][3][0]
    vector = (0.25,) * 1024
    repository = FakeRepository(
        corpus=_corpus(
            "staging",
            manifest_hash_value=manifest_hash(request.manifest),
        ),
        attraction_versions=(
            _version_record(metadata_b),
            _version_record(metadata_a),
        ),
        chunk_rows=(_chunk_row(chunk_b), _chunk_row(chunk_a)),
        reuse_embeddings=(_previous_embedding(chunk_a, vector=vector),),
        final_attraction_versions=(
            _version_record(metadata_b),
            _version_record(metadata_a),
        ),
        final_chunk_rows=(
            _embedded_row(chunk_b, vector),
            _embedded_row(chunk_a, vector),
        ),
        allow_task5_operations=True,
    )
    importer = _importer_module().RagV2Importer(
        identity_source=FakeIdentitySource(
            resolutions={
                "xiamen:gulangyu": _CANDIDATE_ID,
                "xiamen:gulangyu:extra": _OTHER_ID,
            }
        ),
        repository=repository,
        chunker=FakeChunker(chunks=(), chunks_by_attraction=chunks_by_attraction),
        passage_embedder=FakeTask5PassageEmbedder(vector=vector),
    )

    result = importer.import_corpus(request)

    assert result.ready_for_activation is True
    assert result.chunk_rows == (
        _embedded_row(chunk_a, vector),
        _embedded_row(chunk_b, vector),
    )
    assert repository.forbidden_calls == []
