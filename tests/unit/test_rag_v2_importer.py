from __future__ import annotations

from dataclasses import FrozenInstanceError, fields
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
    def __init__(self, *, chunks: tuple[SemanticChunk, ...]) -> None:
        self.chunks = chunks
        self.calls: list[dict[str, object]] = []

    def chunk(
        self,
        section: SemanticSection,
        *,
        attraction: AttractionVersionMetadata,
    ) -> tuple[SemanticChunk, ...]:
        self.calls.append({"section": section, "attraction": attraction})
        return self.chunks


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

    def create_corpus_version(self, **kwargs: object) -> CorpusVersion:
        self.create_calls.append(kwargs)
        if self.create_error is not None:
            raise self.create_error
        return self.corpus

    def list_attraction_versions(
        self,
        *,
        corpus_version_id: UUID,
    ) -> tuple[AttractionVersionRecord, ...]:
        self.attraction_version_calls.append(corpus_version_id)
        if self.snapshot_error is not None:
            raise self.snapshot_error
        return self.attraction_versions

    def list_chunk_rows(self, *, corpus_version_id: UUID) -> tuple[ChunkRow, ...]:
        self.chunk_row_calls.append(corpus_version_id)
        if self.snapshot_error is not None:
            raise self.snapshot_error
        return self.chunk_rows


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
