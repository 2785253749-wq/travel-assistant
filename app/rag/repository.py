from __future__ import annotations

from typing import Protocol, Sequence

import httpx
from postgrest.exceptions import APIError
from pydantic import ValidationError

from app.core.config import Settings, get_settings
from app.core.logging import database_operation
from app.rag.embedding import RagUnavailable
from app.rag.models import KnowledgeChunk, KnowledgeDocument, RetrievedChunk


class KnowledgeStore(Protocol):
    def upsert_document(
        self, document: KnowledgeDocument, chunks: Sequence[KnowledgeChunk]
    ) -> int: ...


class KnowledgeRepository:
    """Private pgvector store, constructed exclusively with Supabase's service key."""

    def __init__(self, *, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        if self._settings.supabase_url is None or self._settings.supabase_service_key is None:
            raise RuntimeError("RAG knowledge storage requires Supabase service-role configuration")
        from supabase import create_client

        self._client = create_client(
            str(self._settings.supabase_url),
            self._settings.supabase_service_key.get_secret_value(),
        )

    def upsert_document(
        self, document: KnowledgeDocument, chunks: Sequence[KnowledgeChunk]
    ) -> int:
        del document  # Chunks deliberately contain the only persisted document fields.
        rows = [
            {
                field: chunk.model_dump(mode="json")[field]
                for field in (
                    "chunk_id",
                    "document_id",
                    "document_version",
                    "region",
                    "topic",
                    "content",
                    "source_label",
                    "reviewed_on",
                    "embedding",
                )
            }
            for chunk in chunks
        ]
        if not rows:
            return 0
        with database_operation("rag.knowledge_chunks.upsert"):
            response = (
                self._client.table("knowledge_chunks")
                .upsert(rows, on_conflict="chunk_id", ignore_duplicates=True)
                .execute()
            )
        return len(response.data or [])

    def search(
        self, query_vector: Sequence[float], region: str | None, limit: int
    ) -> list[RetrievedChunk]:
        if not 1 <= limit <= 20:
            raise ValueError("limit must be between 1 and 20")
        if len(query_vector) != 1024:
            raise ValueError("query_vector must contain 1024 dimensions")
        try:
            with database_operation("rag.knowledge_chunks.search"):
                response = self._client.rpc(
                    "match_knowledge_chunks",
                    {
                        "query_embedding": list(query_vector),
                        "filter_region": region,
                        "match_count": limit,
                    },
                ).execute()
            if not isinstance(response.data, list):
                raise RagUnavailable
            return [RetrievedChunk.model_validate(row) for row in response.data]
        except (APIError, httpx.HTTPError, ValidationError):
            raise RagUnavailable from None

    def reserve(self, requested: int, limit: int) -> bool:
        if requested <= 0 or limit <= 0:
            raise ValueError("requested and limit must be positive")
        if requested > limit:
            return False
        try:
            with database_operation("rag.embedding_quota.reserve"):
                response = self._client.rpc(
                    "reserve_rag_embedding_quota",
                    {"requested": requested, "daily_limit": limit},
                ).execute()
        except (APIError, httpx.HTTPError):
            raise RagUnavailable from None
        return _reserved(response.data)


def _reserved(data: object) -> bool:
    if data is True or data is False:
        return data
    if isinstance(data, list) and len(data) == 1:
        value = data[0]
        if isinstance(value, bool):
            return value
        if isinstance(value, dict):
            reserved = value.get("reserve_rag_embedding_quota")
            if isinstance(reserved, bool):
                return reserved
    raise RagUnavailable
