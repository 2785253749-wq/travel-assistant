from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Protocol
from uuid import UUID

from app.rag_v2.models import ChunkType


class QueryEmbedder(Protocol):
    def embed_query(self, query: str) -> tuple[float, ...]: ...


@dataclass(frozen=True)
class RetrievalEvidence:
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


@dataclass(frozen=True)
class RetrievalResult:
    query: str
    evidence: tuple[RetrievalEvidence, ...]
