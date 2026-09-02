from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import UUID

import httpx
from postgrest.exceptions import APIError
from pydantic import ValidationError

from app.core.config import Settings, get_settings
from app.core.errors import AppError
from app.core.logging import database_operation
from app.rag_v2.models import AttractionLifecycleStatus, StableAttraction


CorpusStatus = Literal["staging", "active", "superseded", "failed"]

_ERROR_MESSAGES = {
    "RAG_V2_NOT_FOUND": "RAG V2 record not found",
    "RAG_V2_VERSION_CONFLICT": "RAG V2 corpus version conflicts with existing data",
    "RAG_V2_INVALID_LIFECYCLE": "RAG V2 lifecycle transition is invalid",
    "RAG_V2_UNAVAILABLE": "RAG V2 persistence is unavailable",
}
_REUSABLE_CORPUS_STATUSES = {"staging", "active", "superseded"}
_LIFECYCLE_ERROR_MESSAGES = {
    "RAG V2 corpus lifecycle transition is invalid",
    "RAG V2 attraction lifecycle transition is invalid",
}


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
        message = getattr(error, "message", None)
        if message in _LIFECYCLE_ERROR_MESSAGES:
            return cls._app_error("RAG_V2_INVALID_LIFECYCLE")
        return cls._app_error("RAG_V2_UNAVAILABLE")
