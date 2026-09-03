from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from urllib.parse import urlparse
from uuid import UUID

from app.core.errors import AppError
from app.rag_v2.chunking import SemanticChunker
from app.rag_v2.hashing import manifest_hash, metadata_hash
from app.rag_v2.identity import AttractionIdentitySource
from app.rag_v2.models import (
    AttractionVersionMetadata,
    ChunkStatus,
    ManifestInput,
    SemanticChunk,
    SemanticSection,
)
from app.rag_v2.passage_embedding import PassageEmbedder
from app.rag_v2.repository import (
    AttractionVersionRecord,
    ChunkInsert,
    ChunkRow,
    CorpusVersion,
    RagV2Repository,
)


_ERROR_MESSAGES = {
    "RAG_V2_VERSION_CONFLICT": "RAG V2 corpus version conflicts with existing data",
}


@dataclass(frozen=True)
class ImportAttraction:
    registry_key: str
    metadata: AttractionVersionMetadata
    sections: tuple[SemanticSection, ...]


@dataclass(frozen=True)
class CorpusImportInput:
    corpus_version_id: UUID
    dataset_key: str
    version_label: str
    manifest: ManifestInput
    attractions: tuple[ImportAttraction, ...]
    retry_failed: bool = False


@dataclass(frozen=True)
class CorpusImportResult:
    corpus: CorpusVersion
    chunk_rows: tuple[ChunkRow, ...]
    ready_for_activation: bool
    reused_chunk_keys: tuple[str, ...]
    embedded_chunk_keys: tuple[str, ...]


class RagV2Importer:
    def __init__(
        self,
        *,
        identity_source: AttractionIdentitySource,
        repository: RagV2Repository,
        chunker: SemanticChunker,
        passage_embedder: PassageEmbedder,
    ) -> None:
        self._identity_source = identity_source
        self._repository = repository
        self._chunker = chunker
        self._passage_embedder = passage_embedder

    def import_corpus(self, request: CorpusImportInput) -> CorpusImportResult:
        resolved_attractions, prepared_chunks, prepared_manifest_hash = self._prepare_request(
            request
        )

        corpus = self._repository.create_corpus_version(
            corpus_version_id=request.corpus_version_id,
            dataset_key=request.dataset_key,
            version_label=request.version_label,
            manifest_hash=prepared_manifest_hash,
        )
        if (
            corpus.dataset_key != request.dataset_key
            or corpus.version_label != request.version_label
            or corpus.manifest_hash != prepared_manifest_hash
        ):
            raise self._version_conflict()

        if corpus.status in {"active", "superseded"}:
            return CorpusImportResult(
                corpus=corpus,
                chunk_rows=(),
                ready_for_activation=False,
                reused_chunk_keys=(),
                embedded_chunk_keys=(),
            )
        if corpus.status == "failed":
            raise self._version_conflict()
        if corpus.status != "staging":
            raise ValueError("unsupported corpus status")

        attraction_versions = self._repository.list_attraction_versions(
            corpus_version_id=corpus.corpus_version_id
        )
        chunk_rows = self._repository.list_chunk_rows(
            corpus_version_id=corpus.corpus_version_id
        )
        expected_versions = self._expected_attraction_versions(
            corpus_version_id=corpus.corpus_version_id,
            attractions=resolved_attractions,
        )
        self._reconcile_attraction_versions(
            corpus_version_id=corpus.corpus_version_id,
            expected=expected_versions,
            existing=attraction_versions,
        )
        expected_chunks = self._expected_chunk_rows(
            corpus_version_id=corpus.corpus_version_id,
            chunks=prepared_chunks,
        )
        inserted_chunks = self._reconcile_chunks(
            corpus_version_id=corpus.corpus_version_id,
            expected=expected_chunks,
            existing=chunk_rows,
        )
        return CorpusImportResult(
            corpus=corpus,
            chunk_rows=tuple(
                sorted(
                    chunk_rows + inserted_chunks,
                    key=lambda row: (
                        str(row.attraction_id),
                        row.chunk_type.value,
                        row.ordinal,
                        row.chunk_key,
                    ),
                )
            ),
            ready_for_activation=False,
            reused_chunk_keys=(),
            embedded_chunk_keys=(),
        )

    def _prepare_request(
        self,
        request: CorpusImportInput,
    ) -> tuple[
        tuple[ImportAttraction, ...],
        tuple[SemanticChunk, ...],
        str,
    ]:
        self._validate_request_header(request)
        self._validate_manifest_profile(request.manifest)

        registry_keys = [attraction.registry_key for attraction in request.attractions]
        if len(set(registry_keys)) != len(registry_keys):
            raise ValueError("registry keys must be unique")

        resolved_attractions = tuple(
            self._resolve_attraction(attraction)
            for attraction in request.attractions
        )
        prepared_chunks: list[SemanticChunk] = []
        for attraction in resolved_attractions:
            for section in attraction.sections:
                self._validate_source(section)
                prepared_chunks.extend(
                    self._chunker.chunk(section, attraction=attraction.metadata)
                )

        chunks = tuple(prepared_chunks)
        self._validate_manifest_artifact(
            request.manifest,
            resolved_attractions=resolved_attractions,
            prepared_chunks=chunks,
        )
        return resolved_attractions, chunks, manifest_hash(request.manifest)

    @staticmethod
    def _validate_request_header(request: CorpusImportInput) -> None:
        if not isinstance(request.dataset_key, str) or not request.dataset_key.strip():
            raise ValueError("dataset_key must not be blank")
        if not isinstance(request.version_label, str) or not request.version_label.strip():
            raise ValueError("version_label must not be blank")
        if request.dataset_key != request.manifest.dataset_key:
            raise ValueError("request and manifest dataset keys must match")
        if type(request.retry_failed) is not bool:
            raise ValueError("retry_failed must be a bool")

    @staticmethod
    def _validate_manifest_profile(manifest: ManifestInput) -> None:
        profile = manifest.embedding_profile
        task = getattr(profile.task, "value", profile.task)
        if (
            profile.model != "jina-embeddings-v3"
            or task != "retrieval.passage"
            or profile.dimensions != 1024
            or profile.input_schema_version != "rag-v2-embedding-input-v1"
        ):
            raise ValueError("manifest requires the approved RAG V2 document embedding profile")

    def _resolve_attraction(self, attraction: ImportAttraction) -> ImportAttraction:
        resolved_id = self._identity_source.resolve(attraction.registry_key)
        if resolved_id is None:
            resolved_id = self._identity_source.allocate(attraction.registry_key)
        elif attraction.metadata.attraction_id != resolved_id:
            raise ValueError("metadata attraction ID does not match stable identity")

        if any(section.attraction_id != attraction.metadata.attraction_id for section in attraction.sections):
            raise ValueError("section and metadata attraction IDs must match")
        chunk_types = [section.chunk_type for section in attraction.sections]
        if len(set(chunk_types)) != len(chunk_types):
            raise ValueError("section chunk types must be unique")

        if attraction.metadata.attraction_id == resolved_id:
            return attraction
        metadata = attraction.metadata.model_copy(update={"attraction_id": resolved_id})
        sections = tuple(
            section.model_copy(update={"attraction_id": resolved_id})
            for section in attraction.sections
        )
        return ImportAttraction(
            registry_key=attraction.registry_key,
            metadata=metadata,
            sections=sections,
        )

    @staticmethod
    def _validate_source(section: SemanticSection) -> None:
        source_label = getattr(section, "source_label", None)
        source_type = getattr(section, "source_type", None)
        reviewed_on = getattr(section, "reviewed_on", None)
        source_url = getattr(section, "source_url", None)
        if not isinstance(source_label, str) or not source_label.strip():
            raise ValueError("source_label must not be blank")
        if not isinstance(source_type, str) or not source_type.strip():
            raise ValueError("source_type must not be blank")
        if isinstance(reviewed_on, datetime) or not isinstance(reviewed_on, date):
            raise ValueError("reviewed_on must be a date")
        if not isinstance(source_url, str):
            raise ValueError("source_url must be an absolute HTTPS URL")
        try:
            parsed = urlparse(source_url)
            hostname = parsed.hostname
        except ValueError:
            raise ValueError("source_url must be an absolute HTTPS URL") from None
        if (
            parsed.scheme != "https"
            or not parsed.netloc
            or not hostname
            or parsed.username is not None
            or parsed.password is not None
        ):
            raise ValueError("source_url must be an absolute HTTPS URL")

    @staticmethod
    def _validate_manifest_artifact(
        manifest: ManifestInput,
        *,
        resolved_attractions: tuple[ImportAttraction, ...],
        prepared_chunks: tuple[SemanticChunk, ...],
    ) -> None:
        expected_attractions = {
            attraction.metadata.attraction_id: attraction
            for attraction in resolved_attractions
        }
        manifest_attractions = {
            attraction.attraction_id: attraction for attraction in manifest.attractions
        }
        if set(expected_attractions) != set(manifest_attractions):
            raise ValueError("manifest attraction identities do not match request")

        chunks_by_attraction: dict[UUID, list[SemanticChunk]] = {}
        for chunk in prepared_chunks:
            chunks_by_attraction.setdefault(chunk.attraction_id, []).append(chunk)

        for attraction_id, expected in expected_attractions.items():
            manifest_attraction = manifest_attractions[attraction_id]
            if manifest_attraction.metadata_hash != metadata_hash(expected.metadata):
                raise ValueError("manifest metadata hash does not match request")
            expected_chunks = {
                (chunk.chunk_key, chunk.chunk_type, chunk.ordinal): chunk
                for chunk in chunks_by_attraction.get(attraction_id, [])
            }
            actual_chunks = {
                (chunk.chunk_key, chunk.chunk_type, chunk.ordinal): chunk
                for chunk in manifest_attraction.chunks
            }
            if set(expected_chunks) != set(actual_chunks):
                raise ValueError("manifest chunk identities do not match request")
            source_by_chunk_type = {
                section.chunk_type: section for section in expected.sections
            }
            for identity, expected_chunk in expected_chunks.items():
                actual_chunk = actual_chunks[identity]
                source = source_by_chunk_type.get(expected_chunk.chunk_type)
                if source is None:
                    raise ValueError("manifest chunk type does not match request")
                if (
                    actual_chunk.content_hash != expected_chunk.content_hash
                    or actual_chunk.embedding_input_hash
                    != expected_chunk.embedding_input_hash
                    or actual_chunk.source_label != source.source_label
                    or actual_chunk.source_url != source.source_url
                    or actual_chunk.source_type != source.source_type
                    or actual_chunk.reviewed_on != source.reviewed_on
                ):
                    raise ValueError("manifest chunk artifact does not match request")

    @staticmethod
    def _expected_attraction_versions(
        *,
        corpus_version_id: UUID,
        attractions: tuple[ImportAttraction, ...],
    ) -> tuple[AttractionVersionRecord, ...]:
        return tuple(
            AttractionVersionRecord(
                corpus_version_id=corpus_version_id,
                metadata=attraction.metadata,
                metadata_hash=metadata_hash(attraction.metadata),
            )
            for attraction in attractions
        )

    def _reconcile_attraction_versions(
        self,
        *,
        corpus_version_id: UUID,
        expected: tuple[AttractionVersionRecord, ...],
        existing: tuple[AttractionVersionRecord, ...],
    ) -> None:
        existing_by_identity = {
            (record.corpus_version_id, record.metadata.attraction_id): record
            for record in existing
        }
        missing: list[AttractionVersionRecord] = []
        for expected_record in expected:
            identity = (
                expected_record.corpus_version_id,
                expected_record.metadata.attraction_id,
            )
            existing_record = existing_by_identity.get(identity)
            if existing_record is None:
                missing.append(expected_record)
                continue
            if not self._attraction_version_matches(
                expected_record,
                existing_record,
            ):
                self._raise_version_conflict(corpus_version_id)

        if missing:
            self._repository.insert_attraction_versions(tuple(missing))

    @staticmethod
    def _attraction_version_matches(
        expected: AttractionVersionRecord,
        existing: AttractionVersionRecord,
    ) -> bool:
        return (
            expected.corpus_version_id == existing.corpus_version_id
            and expected.metadata == existing.metadata
            and expected.metadata_hash == existing.metadata_hash
        )

    @staticmethod
    def _expected_chunk_rows(
        *,
        corpus_version_id: UUID,
        chunks: tuple[SemanticChunk, ...],
    ) -> tuple[ChunkRow, ...]:
        return tuple(
            ChunkRow(
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
            for chunk in chunks
        )

    def _reconcile_chunks(
        self,
        *,
        corpus_version_id: UUID,
        expected: tuple[ChunkRow, ...],
        existing: tuple[ChunkRow, ...],
    ) -> tuple[ChunkRow, ...]:
        existing_by_key = {
            (row.corpus_version_id, row.chunk_key): row for row in existing
        }
        missing: list[ChunkInsert] = []
        for expected_row in expected:
            existing_row = existing_by_key.get(
                (expected_row.corpus_version_id, expected_row.chunk_key)
            )
            if existing_row is None:
                missing.append(
                    ChunkInsert(
                        corpus_version_id=corpus_version_id,
                        chunk=self._semantic_chunk_from_row(expected_row),
                    )
                )
                continue
            if not self._chunk_row_matches(expected_row, existing_row):
                self._raise_version_conflict(corpus_version_id)

        if not missing:
            return ()
        return self._repository.insert_chunks(tuple(missing))

    @staticmethod
    def _semantic_chunk_from_row(row: ChunkRow) -> SemanticChunk:
        return SemanticChunk(
            chunk_key=row.chunk_key,
            attraction_id=row.attraction_id,
            chunk_type=row.chunk_type,
            ordinal=row.ordinal,
            normalized_content=row.content,
            content_hash=row.content_hash,
            embedding_input_hash=row.embedding_input_hash,
            source_label=row.source_label,
            source_url=row.source_url,
            source_type=row.source_type,
            reviewed_on=row.reviewed_on,
        )

    @staticmethod
    def _chunk_row_matches(expected: ChunkRow, existing: ChunkRow) -> bool:
        return (
            expected.corpus_version_id == existing.corpus_version_id
            and expected.chunk_key == existing.chunk_key
            and expected.attraction_id == existing.attraction_id
            and expected.chunk_type == existing.chunk_type
            and expected.ordinal == existing.ordinal
            and expected.content == existing.content
            and expected.content_hash == existing.content_hash
            and expected.embedding_input_hash == existing.embedding_input_hash
            and expected.embedding_input_schema_version
            == existing.embedding_input_schema_version
            and expected.source_label == existing.source_label
            and expected.source_url == existing.source_url
            and expected.source_type == existing.source_type
            and expected.reviewed_on == existing.reviewed_on
            and expected.embedding_model == existing.embedding_model
            and expected.embedding_task == existing.embedding_task
            and expected.embedding_dimensions == existing.embedding_dimensions
        )

    def _raise_version_conflict(self, corpus_version_id: UUID) -> None:
        self._repository.mark_corpus_failed(
            corpus_version_id=corpus_version_id,
        )
        raise self._version_conflict()

    @staticmethod
    def _version_conflict() -> AppError:
        return AppError(
            "RAG_V2_VERSION_CONFLICT",
            _ERROR_MESSAGES["RAG_V2_VERSION_CONFLICT"],
        )
