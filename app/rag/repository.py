from __future__ import annotations

from typing import Any, Protocol, Sequence

from app.core.config import Settings, get_settings
from app.rag.models import KnowledgeChunk, KnowledgeDocument, RetrievedChunk


class KnowledgeStore(Protocol):
    def upsert_document(
        self, document: KnowledgeDocument, chunks: Sequence[KnowledgeChunk]
    ) -> int: ...


class KnowledgeRepository:
    """Private pgvector store, constructed exclusively with Supabase's service key."""

    def __init__(self, *, settings: Settings | None = None, client: Any | None = None) -> None:
        self._settings = settings or get_settings()
        if client is None:
            if self._settings.supabase_url is None or self._settings.supabase_service_key is None:
                raise RuntimeError("RAG knowledge storage requires Supabase service-role configuration")
            from supabase import create_client

            client = create_client(
                str(self._settings.supabase_url),
                self._settings.supabase_service_key.get_secret_value(),
            )
        self._client = client

    def upsert_document(
        self, document: KnowledgeDocument, chunks: Sequence[KnowledgeChunk]
    ) -> int:
        del document  # Chunks deliberately contain the only persisted document fields.
        rows = [chunk.model_dump(mode="json") for chunk in chunks]
        if not rows:
            return 0
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
        response = self._client.rpc(
            "match_knowledge_chunks",
            {
                "query_embedding": list(query_vector),
                "filter_region": region,
                "match_count": limit,
            },
        ).execute()
        return [RetrievedChunk.model_validate(row) for row in response.data or []]
