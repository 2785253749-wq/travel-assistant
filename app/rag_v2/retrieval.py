from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import math
from typing import Protocol
from uuid import UUID

from app.rag_v2.models import ChunkType, DestinationLevel
from app.rag_v2.repository import RagV2Candidate, RagV2Repository


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


class RetrievalService:
    def __init__(
        self,
        *,
        embedder: QueryEmbedder,
        repository: RagV2Repository,
    ) -> None:
        self._embedder = embedder
        self._repository = repository

    def retrieve(
        self,
        *,
        query: str,
        dataset_key: str,
        destination_code: str | None = None,
        destination_level: DestinationLevel | None = None,
        province_code: str | None = None,
        attraction_id: UUID | None = None,
        candidate_k: int = 40,
        final_k: int = 6,
        score_threshold: float = 0.60,
    ) -> RetrievalResult:
        if not isinstance(query, str):
            raise ValueError("query must be a non-empty string")
        normalized_query = query.strip()
        if not normalized_query:
            raise ValueError("query must be a non-empty string")

        if not isinstance(final_k, int) or isinstance(final_k, bool) or final_k < 1:
            raise ValueError("final_k must be at least 1")

        if isinstance(score_threshold, bool) or not isinstance(
            score_threshold, (int, float)
        ):
            raise ValueError("score_threshold must be finite")
        try:
            threshold = float(score_threshold)
        except (TypeError, ValueError, OverflowError):
            raise ValueError("score_threshold must be finite") from None
        if not math.isfinite(threshold):
            raise ValueError("score_threshold must be finite")

        query_vector = self._embedder.embed_query(normalized_query)
        candidates = self._repository.match_chunks(
            dataset_key=dataset_key,
            query_embedding=query_vector,
            destination_code=destination_code,
            destination_level=destination_level,
            province_code=province_code,
            attraction_id=attraction_id,
            candidate_k=candidate_k,
        )

        thresholded = tuple(
            candidate for candidate in candidates if candidate.score >= threshold
        )

        seen_hashes: set[str] = set()
        deduplicated: list[RagV2Candidate] = []
        for candidate in thresholded:
            if candidate.content_hash in seen_hashes:
                continue
            seen_hashes.add(candidate.content_hash)
            deduplicated.append(candidate)

        selected: list[RagV2Candidate] = []
        selected_indexes: set[int] = set()
        seen_attractions: set[UUID] = set()

        for index, candidate in enumerate(deduplicated):
            if candidate.attraction_id in seen_attractions:
                continue

            seen_attractions.add(candidate.attraction_id)
            selected.append(candidate)
            selected_indexes.add(index)

            if len(selected) == final_k:
                break

        if len(selected) < final_k:
            for index, candidate in enumerate(deduplicated):
                if index in selected_indexes:
                    continue

                selected.append(candidate)

                if len(selected) == final_k:
                    break

        evidence = tuple(
            RetrievalEvidence(
                attraction_id=candidate.attraction_id,
                chunk_key=candidate.chunk_key,
                chunk_type=candidate.chunk_type,
                content=candidate.content,
                content_hash=candidate.content_hash,
                source_label=candidate.source_label,
                source_url=candidate.source_url,
                source_type=candidate.source_type,
                reviewed_on=candidate.reviewed_on,
                score=candidate.score,
            )
            for candidate in selected
        )
        return RetrievalResult(query=normalized_query, evidence=evidence)
