from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal, Sequence
from uuid import UUID

import httpx
from postgrest import ReturnMethod
from postgrest.exceptions import APIError
from pydantic import ValidationError

from app.core.config import Settings, get_settings
from app.core.errors import AppError
from app.core.logging import database_operation
from app.rag_v2.models import (
    AttractionLifecycleStatus,
    AttractionVersionMetadata,
    AttractionVersionStatus,
    ChunkStatus,
    ChunkType,
    Destination,
    DestinationLevel,
    SemanticChunk,
    StableAttraction,
)


CorpusStatus = Literal["staging", "active", "superseded", "failed"]

_ERROR_MESSAGES = {
    "RAG_V2_NOT_FOUND": "RAG V2 record not found",
    "RAG_V2_VERSION_CONFLICT": "RAG V2 corpus version conflicts with existing data",
    "RAG_V2_ACTIVATION_CONFLICT": "RAG V2 corpus activation conflict",
    "RAG_V2_INVALID_LIFECYCLE": "RAG V2 lifecycle transition is invalid",
    "RAG_V2_UNAVAILABLE": "RAG V2 persistence is unavailable",
}
_REUSABLE_CORPUS_STATUSES = {"staging", "active", "superseded"}
_LIFECYCLE_ERROR_MESSAGES = {
    "RAG V2 corpus lifecycle transition is invalid",
    "RAG V2 attraction lifecycle transition is invalid",
}
_TASK7_LIFECYCLE_ERROR_PREFIXES = {
    "RAG V2 attraction version updates are forbidden",
    "RAG V2 attraction version mutation requires staging",
    "RAG V2 chunk insert is forbidden for terminal corpus",
    "RAG V2 chunk insert requires status = 'staging'",
    "RAG V2 chunk update is forbidden for terminal corpus",
    "RAG V2 chunk update requires status = 'staging'",
    "RAG V2 chunk retry reset must clear embedding errors",
    "RAG V2 chunk embedding transition is invalid",
}
_TASK8_ACTIVATION_ERROR_PREFIXES = {
    "RAG_V2_NOT_FOUND:": "RAG_V2_NOT_FOUND",
    "RAG_V2_VERSION_CONFLICT:": "RAG_V2_VERSION_CONFLICT",
    "RAG_V2_ACTIVATION_CONFLICT:": "RAG_V2_ACTIVATION_CONFLICT",
    "RAG_V2_INVALID_LIFECYCLE:": "RAG_V2_INVALID_LIFECYCLE",
}
_RAG_V2_EMBEDDING_MODEL = "jina-embeddings-v3"
_RAG_V2_EMBEDDING_TASK = "retrieval.passage"
_RAG_V2_EMBEDDING_DIMENSIONS = 1024
_RAG_V2_EMBEDDING_INPUT_SCHEMA = "rag-v2-embedding-input-v1"


@dataclass(frozen=True)
class CorpusVersion:
    corpus_version_id: UUID
    dataset_key: str
    version_label: str
    manifest_hash: str
    status: CorpusStatus
    created_at: datetime
    activated_at: datetime | None
    superseded_at: datetime | None


@dataclass(frozen=True)
class AttractionVersionRecord:
    corpus_version_id: UUID
    metadata: AttractionVersionMetadata
    metadata_hash: str


@dataclass(frozen=True)
class ChunkInsert:
    corpus_version_id: UUID
    chunk: SemanticChunk


@dataclass(frozen=True)
class ChunkRow:
    corpus_version_id: UUID
    attraction_id: UUID
    chunk_key: str
    chunk_type: ChunkType
    ordinal: int
    content: str
    content_hash: str
    embedding_input_hash: str
    embedding_input_schema_version: str
    source_label: str
    source_url: str
    source_type: str
    reviewed_on: date
    embedding_model: str
    embedding_task: str
    embedding_dimensions: int
    embedding: tuple[float, ...] | None
    status: ChunkStatus
    embedding_error_code: str | None
    embedding_error_message: str | None


@dataclass(frozen=True)
class RagV2Candidate:
    corpus_version_id: UUID
    attraction_id: UUID
    chunk_key: str
    chunk_type: ChunkType
    content: str
    content_hash: str
    source_label: str
    source_url: str
    source_type: str
    reviewed_on: date
    score: float


class RagV2Repository:
    """Thin service-role adapter for corpus and stable-attraction persistence."""

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        client: object | None = None,
    ) -> None:
        if client is not None:
            self._client = client
            return

        configured = settings or get_settings()
        if configured.supabase_url is None or configured.supabase_service_key is None:
            raise RuntimeError(
                "RAG V2 repository requires Supabase service-role configuration"
            )

        from supabase import create_client

        self._client = create_client(
            str(configured.supabase_url),
            configured.supabase_service_key.get_secret_value(),
        )

    def create_corpus_version(
        self,
        *,
        corpus_version_id: UUID,
        dataset_key: str,
        version_label: str,
        manifest_hash: str,
    ) -> CorpusVersion:
        try:
            with database_operation("rag_v2.corpus.create"):
                existing = self._select_one(
                    "rag_corpus_versions",
                    (
                        ("dataset_key", dataset_key),
                        ("version_label", version_label),
                    ),
                )
                if existing is not None:
                    return self._resolve_existing_corpus(
                        existing,
                        dataset_key=dataset_key,
                        version_label=version_label,
                        manifest_hash=manifest_hash,
                    )

                payload = {
                    "corpus_version_id": str(corpus_version_id),
                    "dataset_key": dataset_key,
                    "version_label": version_label,
                    "manifest_hash": manifest_hash,
                    "status": "staging",
                }
                try:
                    response = (
                        self._client.table("rag_corpus_versions")
                        .insert(payload)
                        .execute()
                    )
                except APIError as exc:
                    if getattr(exc, "code", None) != "23505":
                        raise self._mapped_api_error(exc) from None

                    winner = self._select_one(
                        "rag_corpus_versions",
                        (
                            ("dataset_key", dataset_key),
                            ("version_label", version_label),
                        ),
                    )
                    if winner is None:
                        raise self._app_error("RAG_V2_UNAVAILABLE") from None
                    return self._resolve_existing_corpus(
                        winner,
                        dataset_key=dataset_key,
                        version_label=version_label,
                        manifest_hash=manifest_hash,
                    )
                except httpx.HTTPError:
                    raise self._app_error("RAG_V2_UNAVAILABLE") from None

                return self._corpus_from_response(response)
        except AppError:
            raise
        except (APIError, httpx.HTTPError, KeyError, TypeError, ValueError, ValidationError):
            raise self._app_error("RAG_V2_UNAVAILABLE") from None

    def get_corpus_version(
        self,
        *,
        corpus_version_id: UUID,
    ) -> CorpusVersion | None:
        try:
            with database_operation("rag_v2.corpus.get"):
                row = self._select_one(
                    "rag_corpus_versions",
                    (("corpus_version_id", str(corpus_version_id)),),
                )
                return None if row is None else self._corpus_from_row(row)
        except AppError:
            raise
        except (APIError, httpx.HTTPError, KeyError, TypeError, ValueError, ValidationError):
            raise self._app_error("RAG_V2_UNAVAILABLE") from None

    def mark_corpus_failed(
        self,
        *,
        corpus_version_id: UUID,
    ) -> CorpusVersion:
        try:
            with database_operation("rag_v2.corpus.mark_failed"):
                response = (
                    self._client.table("rag_corpus_versions")
                    .update({"status": "failed"})
                    .eq("corpus_version_id", str(corpus_version_id))
                    .execute()
                )
                row = self._first_row(response)
                if row is None:
                    raise self._app_error("RAG_V2_NOT_FOUND")
                return self._corpus_from_row(row)
        except AppError:
            raise
        except APIError as exc:
            raise self._mapped_api_error(exc) from None
        except (httpx.HTTPError, KeyError, TypeError, ValueError, ValidationError):
            raise self._app_error("RAG_V2_UNAVAILABLE") from None

    def get_attraction(
        self,
        *,
        attraction_id: UUID,
    ) -> StableAttraction | None:
        try:
            with database_operation("rag_v2.attraction.get"):
                row = self._select_one(
                    "rag_attractions",
                    (("attraction_id", str(attraction_id)),),
                )
                return None if row is None else self._attraction_from_row(row)
        except AppError:
            raise
        except (APIError, httpx.HTTPError, KeyError, TypeError, ValueError, ValidationError):
            raise self._app_error("RAG_V2_UNAVAILABLE") from None

    def insert_attraction(self, attraction: StableAttraction) -> StableAttraction:
        try:
            with database_operation("rag_v2.attraction.insert"):
                response = (
                    self._client.table("rag_attractions")
                    .insert(self._attraction_payload(attraction))
                    .execute()
                )
                row = self._first_row(response)
                if row is None:
                    raise self._app_error("RAG_V2_UNAVAILABLE")
                return self._attraction_from_row(row)
        except AppError:
            raise
        except APIError as exc:
            raise self._mapped_api_error(exc) from None
        except (httpx.HTTPError, KeyError, TypeError, ValueError, ValidationError):
            raise self._app_error("RAG_V2_UNAVAILABLE") from None

    def update_attraction_lifecycle(
        self,
        *,
        attraction_id: UUID,
        lifecycle_status: AttractionLifecycleStatus,
        retired_at: datetime | None = None,
        merged_into_attraction_id: UUID | None = None,
    ) -> StableAttraction:
        status = AttractionLifecycleStatus(lifecycle_status)
        payload = {
            "lifecycle_status": status.value,
            "retired_at": retired_at.isoformat() if retired_at is not None else None,
            "merged_into_attraction_id": (
                str(merged_into_attraction_id)
                if merged_into_attraction_id is not None
                else None
            ),
        }
        try:
            with database_operation("rag_v2.attraction.lifecycle"):
                response = (
                    self._client.table("rag_attractions")
                    .update(payload)
                    .eq("attraction_id", str(attraction_id))
                    .execute()
                )
                row = self._first_row(response)
                if row is None:
                    raise self._app_error("RAG_V2_NOT_FOUND")
                return self._attraction_from_row(row)
        except AppError:
            raise
        except APIError as exc:
            raise self._mapped_api_error(exc) from None
        except (httpx.HTTPError, KeyError, TypeError, ValueError, ValidationError):
            raise self._app_error("RAG_V2_UNAVAILABLE") from None

    def insert_attraction_versions(
        self,
        records: Sequence[AttractionVersionRecord],
    ) -> tuple[AttractionVersionRecord, ...]:
        records = tuple(records)
        if not records:
            return ()
        identities = tuple(
            (record.corpus_version_id, record.metadata.attraction_id)
            for record in records
        )
        if len(set(identities)) != len(identities):
            raise self._app_error("RAG_V2_VERSION_CONFLICT")

        payload = [self._attraction_version_payload(record) for record in records]
        try:
            with database_operation("rag_v2.attraction_version.insert"):
                response = (
                    self._client.table("rag_attraction_versions")
                    .insert(payload, returning=ReturnMethod.representation)
                    .execute()
                )
                rows = self._batch_rows(response)
                decoded = tuple(self._attraction_version_from_row(row) for row in rows)
                return self._ordered_version_rows(decoded, identities, len(records))
        except AppError:
            raise
        except APIError as exc:
            raise self._mapped_api_error(exc) from None
        except (httpx.HTTPError, KeyError, TypeError, ValueError, ValidationError):
            raise self._app_error("RAG_V2_UNAVAILABLE") from None

    def insert_chunks(
        self,
        inserts: Sequence[ChunkInsert],
    ) -> tuple[ChunkRow, ...]:
        inserts = tuple(inserts)
        if not inserts:
            return ()
        identities = tuple(
            (item.corpus_version_id, item.chunk.chunk_key) for item in inserts
        )
        if len(set(identities)) != len(identities):
            raise self._app_error("RAG_V2_VERSION_CONFLICT")

        payload = [self._chunk_payload(item) for item in inserts]
        try:
            with database_operation("rag_v2.chunk.insert"):
                response = (
                    self._client.table("rag_attraction_chunks")
                    .insert(payload, returning=ReturnMethod.representation)
                    .execute()
                )
                rows = self._batch_rows(response)
                decoded = tuple(self._chunk_from_row(row) for row in rows)
                return self._ordered_chunk_rows(decoded, identities, len(inserts))
        except AppError:
            raise
        except APIError as exc:
            raise self._mapped_api_error(exc) from None
        except (httpx.HTTPError, KeyError, TypeError, ValueError, ValidationError):
            raise self._app_error("RAG_V2_UNAVAILABLE") from None

    def mark_chunk_embedded(
        self,
        *,
        corpus_version_id: UUID,
        chunk_key: str,
        embedding: Sequence[float],
    ) -> ChunkRow:
        embedding_values = self._validated_embedding(embedding)
        try:
            with database_operation("rag_v2.chunk.mark_embedded"):
                response = (
                    self._client.table("rag_attraction_chunks")
                    .update(
                        {
                            "status": ChunkStatus.embedded.value,
                            "embedding": embedding_values,
                            "embedding_error_code": None,
                            "embedding_error_message": None,
                        }
                    )
                    .eq("corpus_version_id", str(corpus_version_id))
                    .eq("chunk_key", chunk_key)
                    .execute()
                )
                return self._chunk_from_mutation_response(response)
        except AppError:
            raise
        except APIError as exc:
            raise self._mapped_api_error(exc) from None
        except (httpx.HTTPError, KeyError, TypeError, ValueError, ValidationError):
            raise self._app_error("RAG_V2_UNAVAILABLE") from None

    def mark_chunk_embedding_failed(
        self,
        *,
        corpus_version_id: UUID,
        chunk_key: str,
        error_code: str,
        error_message: str | None = None,
    ) -> ChunkRow:
        if not isinstance(error_code, str) or not error_code.strip():
            raise ValueError("error_code must be a non-empty string")
        normalized_error_code = error_code.strip()
        try:
            with database_operation("rag_v2.chunk.mark_failed"):
                response = (
                    self._client.table("rag_attraction_chunks")
                    .update(
                        {
                            "status": ChunkStatus.failed.value,
                            "embedding": None,
                            "embedding_error_code": normalized_error_code,
                            "embedding_error_message": error_message,
                        }
                    )
                    .eq("corpus_version_id", str(corpus_version_id))
                    .eq("chunk_key", chunk_key)
                    .execute()
                )
                return self._chunk_from_mutation_response(response)
        except AppError:
            raise
        except APIError as exc:
            raise self._mapped_api_error(exc) from None
        except (httpx.HTTPError, KeyError, TypeError, ValueError, ValidationError):
            raise self._app_error("RAG_V2_UNAVAILABLE") from None

    def reset_chunk_embedding_for_retry(
        self,
        *,
        corpus_version_id: UUID,
        chunk_key: str,
    ) -> ChunkRow:
        try:
            with database_operation("rag_v2.chunk.reset_for_retry"):
                response = (
                    self._client.table("rag_attraction_chunks")
                    .update(
                        {
                            "status": ChunkStatus.pending.value,
                            "embedding": None,
                            "embedding_error_code": None,
                            "embedding_error_message": None,
                        }
                    )
                    .eq("corpus_version_id", str(corpus_version_id))
                    .eq("chunk_key", chunk_key)
                    .execute()
                )
                return self._chunk_from_mutation_response(response)
        except AppError:
            raise
        except APIError as exc:
            raise self._mapped_api_error(exc) from None
        except (httpx.HTTPError, KeyError, TypeError, ValueError, ValidationError):
            raise self._app_error("RAG_V2_UNAVAILABLE") from None

    def activate_corpus(
        self,
        *,
        dataset_key: str,
        corpus_version_id: UUID,
        expected_active_corpus_version_id: UUID | None = None,
    ) -> None:
        params = {
            "p_dataset_key": dataset_key,
            "p_corpus_version_id": str(corpus_version_id),
            "p_expected_active_corpus_version_id": (
                str(expected_active_corpus_version_id)
                if expected_active_corpus_version_id is not None
                else None
            ),
        }
        try:
            with database_operation("rag_v2.corpus.activate"):
                response = self._client.rpc("activate_rag_v2_corpus", params).execute()
                if response is None or not hasattr(response, "data"):
                    raise self._app_error("RAG_V2_UNAVAILABLE")
        except AppError:
            raise
        except APIError as exc:
            raise self._mapped_activation_api_error(exc) from None
        except httpx.HTTPError:
            raise self._app_error("RAG_V2_UNAVAILABLE") from None
        except (TypeError, ValueError, ValidationError):
            raise self._app_error("RAG_V2_UNAVAILABLE") from None

    def match_chunks(
        self,
        *,
        dataset_key: str,
        query_embedding: Sequence[float],
        destination_code: str | None = None,
        destination_level: DestinationLevel | None = None,
        province_code: str | None = None,
        attraction_id: UUID | None = None,
        candidate_k: int = 40,
    ) -> tuple[RagV2Candidate, ...]:
        embedding_values = self._validated_embedding(query_embedding)
        params = {
            "p_dataset_key": dataset_key,
            "p_query_embedding": embedding_values,
            "p_destination_code": destination_code,
            "p_destination_level": (
                destination_level.value if destination_level is not None else None
            ),
            "p_province_code": province_code,
            "p_attraction_id": (
                str(attraction_id) if attraction_id is not None else None
            ),
            "p_candidate_k": candidate_k,
        }
        try:
            with database_operation("rag_v2.chunk.match"):
                response = self._client.rpc("match_rag_v2_chunks", params).execute()
                rows = self._batch_rows(response)
                return tuple(self._candidate_from_row(row) for row in rows)
        except AppError:
            raise
        except APIError:
            raise self._app_error("RAG_V2_UNAVAILABLE") from None
        except (
            httpx.HTTPError,
            KeyError,
            TypeError,
            ValueError,
            OverflowError,
            ValidationError,
        ):
            raise self._app_error("RAG_V2_UNAVAILABLE") from None

    def _select_one(self, table_name: str, filters: tuple[tuple[str, str], ...]):
        query = self._client.table(table_name).select("*")
        for column, value in filters:
            query = query.eq(column, value)
        response = query.execute()
        return self._first_row(response)

    @staticmethod
    def _first_row(response):
        data = getattr(response, "data", None)
        if isinstance(data, dict):
            return data
        if not isinstance(data, list):
            raise ValueError("database response data must be a list")
        if not data:
            return None
        if not isinstance(data[0], dict):
            raise ValueError("database response row must be an object")
        return data[0]

    @staticmethod
    def _batch_rows(response) -> list[dict]:
        data = getattr(response, "data", None)
        if not isinstance(data, list) or not all(isinstance(row, dict) for row in data):
            raise ValueError("database batch response data must be a list of objects")
        return data

    @staticmethod
    def _attraction_version_payload(record: AttractionVersionRecord) -> dict:
        metadata = record.metadata
        destination = metadata.destination
        return {
            "corpus_version_id": str(record.corpus_version_id),
            "attraction_id": str(metadata.attraction_id),
            "canonical_name": metadata.canonical_name,
            "aliases": list(metadata.aliases),
            "destination_code": destination.destination_code,
            "destination_level": destination.destination_level.value,
            "destination_name": destination.destination_name,
            "province_code": destination.province_code,
            "province_name": destination.province_name,
            "district_name": destination.district_name,
            "category": metadata.category,
            "tags": list(metadata.tags),
            "latitude": destination.latitude,
            "longitude": destination.longitude,
            "status": metadata.status.value,
            "metadata_hash": record.metadata_hash,
        }

    @staticmethod
    def _chunk_payload(item: ChunkInsert) -> dict:
        chunk = item.chunk
        return {
            "corpus_version_id": str(item.corpus_version_id),
            "attraction_id": str(chunk.attraction_id),
            "chunk_key": chunk.chunk_key,
            "chunk_type": chunk.chunk_type.value,
            "ordinal": chunk.ordinal,
            "content": chunk.normalized_content,
            "content_hash": chunk.content_hash,
            "embedding_input_hash": chunk.embedding_input_hash,
            "embedding_input_schema_version": _RAG_V2_EMBEDDING_INPUT_SCHEMA,
            "source_label": chunk.source_label,
            "source_url": chunk.source_url,
            "source_type": chunk.source_type,
            "reviewed_on": chunk.reviewed_on.isoformat(),
            "embedding_model": _RAG_V2_EMBEDDING_MODEL,
            "embedding_task": _RAG_V2_EMBEDDING_TASK,
            "embedding_dimensions": _RAG_V2_EMBEDDING_DIMENSIONS,
            "embedding": None,
            "status": ChunkStatus.pending.value,
            "embedding_error_code": None,
            "embedding_error_message": None,
        }

    @classmethod
    def _ordered_version_rows(
        cls,
        rows: tuple[AttractionVersionRecord, ...],
        identities: tuple[tuple[UUID, UUID], ...],
        expected_count: int,
    ) -> tuple[AttractionVersionRecord, ...]:
        if len(rows) != expected_count:
            raise ValueError("database response count does not match input")
        by_identity = {
            (row.corpus_version_id, row.metadata.attraction_id): row for row in rows
        }
        if len(by_identity) != len(rows) or set(by_identity) != set(identities):
            raise ValueError("database response identities do not match input")
        return tuple(by_identity[identity] for identity in identities)

    @classmethod
    def _ordered_chunk_rows(
        cls,
        rows: tuple[ChunkRow, ...],
        identities: tuple[tuple[UUID, str], ...],
        expected_count: int,
    ) -> tuple[ChunkRow, ...]:
        if len(rows) != expected_count:
            raise ValueError("database response count does not match input")
        by_identity = {
            (row.corpus_version_id, row.chunk_key): row for row in rows
        }
        if len(by_identity) != len(rows) or set(by_identity) != set(identities):
            raise ValueError("database response identities do not match input")
        return tuple(by_identity[identity] for identity in identities)

    @classmethod
    def _attraction_version_from_row(cls, row: dict) -> AttractionVersionRecord:
        aliases = cls._string_tuple(row["aliases"])
        tags = cls._string_tuple(row["tags"])
        metadata = AttractionVersionMetadata(
            attraction_id=UUID(str(row["attraction_id"])),
            canonical_name=cls._required_string(row["canonical_name"]),
            aliases=aliases,
            destination=Destination(
                destination_code=cls._required_string(row["destination_code"]),
                destination_level=DestinationLevel(row["destination_level"]),
                destination_name=cls._required_string(row["destination_name"]),
                province_code=cls._required_string(row["province_code"]),
                province_name=cls._required_string(row["province_name"]),
                district_name=row["district_name"],
                latitude=row["latitude"],
                longitude=row["longitude"],
            ),
            category=row["category"],
            tags=tags,
            status=AttractionVersionStatus(row["status"]),
        )
        return AttractionVersionRecord(
            corpus_version_id=UUID(str(row["corpus_version_id"])),
            metadata=metadata,
            metadata_hash=cls._required_string(row["metadata_hash"]),
        )

    @classmethod
    def _chunk_from_row(cls, row: dict) -> ChunkRow:
        embedding_dimensions = row["embedding_dimensions"]
        if not isinstance(embedding_dimensions, int):
            raise ValueError("invalid embedding dimensions")
        reviewed_on = row["reviewed_on"]
        if isinstance(reviewed_on, datetime):
            raise ValueError("reviewed_on must be a date")
        if not isinstance(reviewed_on, date):
            reviewed_on = date.fromisoformat(str(reviewed_on))
        return ChunkRow(
            corpus_version_id=UUID(str(row["corpus_version_id"])),
            attraction_id=UUID(str(row["attraction_id"])),
            chunk_key=cls._required_string(row["chunk_key"]),
            chunk_type=ChunkType(row["chunk_type"]),
            ordinal=row["ordinal"],
            content=cls._required_string(row["content"]),
            content_hash=cls._required_string(row["content_hash"]),
            embedding_input_hash=cls._required_string(row["embedding_input_hash"]),
            embedding_input_schema_version=cls._required_string(
                row["embedding_input_schema_version"]
            ),
            source_label=cls._required_string(row["source_label"]),
            source_url=cls._required_string(row["source_url"]),
            source_type=cls._required_string(row["source_type"]),
            reviewed_on=reviewed_on,
            embedding_model=cls._required_string(row["embedding_model"]),
            embedding_task=cls._required_string(row["embedding_task"]),
            embedding_dimensions=embedding_dimensions,
            embedding=cls._returned_embedding(row.get("embedding")),
            status=ChunkStatus(row["status"]),
            embedding_error_code=cls._optional_string(row.get("embedding_error_code")),
            embedding_error_message=cls._optional_string(
                row.get("embedding_error_message")
            ),
        )

    @classmethod
    def _candidate_from_row(cls, row: dict) -> RagV2Candidate:
        reviewed_on = row["reviewed_on"]
        if isinstance(reviewed_on, datetime):
            raise ValueError("reviewed_on must be a date")
        if not isinstance(reviewed_on, date):
            reviewed_on = date.fromisoformat(str(reviewed_on))
        return RagV2Candidate(
            corpus_version_id=UUID(str(row["corpus_version_id"])),
            attraction_id=UUID(str(row["attraction_id"])),
            chunk_key=cls._required_string(row["chunk_key"]),
            chunk_type=ChunkType(row["chunk_type"]),
            content=cls._required_string(row["content"]),
            content_hash=cls._required_string(row["content_hash"]),
            source_label=cls._required_string(row["source_label"]),
            source_url=cls._required_string(row["source_url"]),
            source_type=cls._required_string(row["source_type"]),
            reviewed_on=reviewed_on,
            score=float(row["score"]),
        )

    @classmethod
    def _chunk_from_mutation_response(cls, response) -> ChunkRow:
        row = cls._first_row(response)
        if row is None:
            raise cls._app_error("RAG_V2_NOT_FOUND")
        return cls._chunk_from_row(row)

    @staticmethod
    def _validated_embedding(embedding: Sequence[float]) -> list[float]:
        try:
            values = list(embedding)
            normalized = [float(value) for value in values]
        except (TypeError, ValueError, OverflowError):
            raise ValueError("embedding must contain 1024 finite values") from None
        if len(normalized) != _RAG_V2_EMBEDDING_DIMENSIONS or not all(
            math.isfinite(value) for value in normalized
        ):
            raise ValueError("embedding must contain 1024 finite values")
        return normalized

    @classmethod
    def _returned_embedding(cls, value: object) -> tuple[float, ...] | None:
        if value is None:
            return None
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except (TypeError, ValueError):
                raise ValueError("malformed embedding") from None
        if not isinstance(value, (list, tuple)):
            raise ValueError("malformed embedding")
        try:
            normalized = tuple(float(item) for item in value)
        except (TypeError, ValueError, OverflowError):
            raise ValueError("malformed embedding") from None
        if len(normalized) != _RAG_V2_EMBEDDING_DIMENSIONS or not all(
            math.isfinite(item) for item in normalized
        ):
            raise ValueError("malformed embedding")
        return normalized

    @staticmethod
    def _required_string(value: object) -> str:
        if not isinstance(value, str):
            raise ValueError("required database field must be a string")
        return value

    @classmethod
    def _optional_string(cls, value: object) -> str | None:
        return None if value is None else cls._required_string(value)

    @classmethod
    def _string_tuple(cls, value: object) -> tuple[str, ...]:
        if not isinstance(value, (list, tuple)):
            raise ValueError("database array field must be a list")
        return tuple(cls._required_string(item) for item in value)

    @classmethod
    def _corpus_from_response(cls, response) -> CorpusVersion:
        row = cls._first_row(response)
        if row is None:
            raise cls._app_error("RAG_V2_UNAVAILABLE")
        return cls._corpus_from_row(row)

    @classmethod
    def _corpus_from_row(cls, row: dict) -> CorpusVersion:
        status = row["status"]
        if status not in {"staging", "active", "superseded", "failed"}:
            raise ValueError("invalid corpus status")
        dataset_key = row["dataset_key"]
        version_label = row["version_label"]
        manifest_hash = row["manifest_hash"]
        if not all(
            isinstance(value, str)
            for value in (dataset_key, version_label, manifest_hash)
        ):
            raise ValueError("invalid corpus identity fields")
        return CorpusVersion(
            corpus_version_id=UUID(row["corpus_version_id"]),
            dataset_key=dataset_key,
            version_label=version_label,
            manifest_hash=manifest_hash,
            status=status,
            created_at=cls._required_datetime(row["created_at"]),
            activated_at=cls._optional_datetime(row.get("activated_at")),
            superseded_at=cls._optional_datetime(row.get("superseded_at")),
        )

    @classmethod
    def _resolve_existing_corpus(
        cls,
        row: dict,
        *,
        dataset_key: str,
        version_label: str,
        manifest_hash: str,
    ) -> CorpusVersion:
        corpus = cls._corpus_from_row(row)
        if corpus.dataset_key != dataset_key or corpus.version_label != version_label:
            raise cls._app_error("RAG_V2_UNAVAILABLE")
        if corpus.manifest_hash != manifest_hash:
            raise cls._app_error("RAG_V2_VERSION_CONFLICT")
        if corpus.status in _REUSABLE_CORPUS_STATUSES:
            return corpus
        raise cls._app_error("RAG_V2_VERSION_CONFLICT")

    @staticmethod
    def _attraction_payload(attraction: StableAttraction) -> dict[str, str | None]:
        return {
            "attraction_id": str(attraction.attraction_id),
            "lifecycle_status": attraction.lifecycle_status.value,
            "created_at": attraction.created_at.isoformat(),
            "retired_at": (
                attraction.retired_at.isoformat()
                if attraction.retired_at is not None
                else None
            ),
            "merged_into_attraction_id": (
                str(attraction.merged_into_attraction_id)
                if attraction.merged_into_attraction_id is not None
                else None
            ),
        }

    @classmethod
    def _attraction_from_row(cls, row: dict) -> StableAttraction:
        return StableAttraction(
            attraction_id=UUID(row["attraction_id"]),
            lifecycle_status=AttractionLifecycleStatus(row["lifecycle_status"]),
            created_at=cls._required_datetime(row["created_at"]),
            retired_at=cls._optional_datetime(row.get("retired_at")),
            merged_into_attraction_id=cls._optional_uuid(
                row.get("merged_into_attraction_id")
            ),
        )

    @staticmethod
    def _required_datetime(value: object) -> datetime:
        parsed = RagV2Repository._optional_datetime(value)
        if parsed is None:
            raise ValueError("required timestamp is missing")
        return parsed

    @staticmethod
    def _optional_datetime(value: object) -> datetime | None:
        if value is None:
            return None
        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError("timestamp must be timezone-aware")
        return parsed

    @staticmethod
    def _optional_uuid(value: object) -> UUID | None:
        return None if value is None else UUID(str(value))

    @staticmethod
    def _app_error(code: str) -> AppError:
        return AppError(code, _ERROR_MESSAGES[code])

    @classmethod
    def _mapped_api_error(cls, error: APIError) -> AppError:
        code = getattr(error, "code", None)
        message = getattr(error, "message", None)
        if code == "23505":
            return cls._app_error("RAG_V2_VERSION_CONFLICT")
        if code == "P0001" and (
            message in _LIFECYCLE_ERROR_MESSAGES
            or (
                isinstance(message, str)
                and any(
                    message.startswith(prefix)
                    for prefix in _TASK7_LIFECYCLE_ERROR_PREFIXES
                )
            )
        ):
            return cls._app_error("RAG_V2_INVALID_LIFECYCLE")
        return cls._app_error("RAG_V2_UNAVAILABLE")

    @classmethod
    def _mapped_activation_api_error(cls, error: APIError) -> AppError:
        if getattr(error, "code", None) != "P0001":
            return cls._app_error("RAG_V2_UNAVAILABLE")
        message = getattr(error, "message", None)
        if not isinstance(message, str):
            return cls._app_error("RAG_V2_UNAVAILABLE")
        for prefix, error_code in _TASK8_ACTIVATION_ERROR_PREFIXES.items():
            if message.startswith(prefix):
                return cls._app_error(error_code)
        return cls._app_error("RAG_V2_UNAVAILABLE")
