from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol
from uuid import UUID

from app.core.errors import AppError
from app.rag_v2.models import DestinationLevel
from app.rag_v2.retrieval import RetrievalEvidence, RetrievalService


V2KnowledgeStatus = Literal["grounded", "empty", "unavailable"]


@dataclass(frozen=True)
class V2KnowledgeResult:
    status: V2KnowledgeStatus
    reply: str | None
    evidence: tuple[RetrievalEvidence, ...]
    error_code: str | None = None


class V2KnowledgeAnswerer(Protocol):
    def answer(
        self,
        query: str,
        *,
        destination_code: str | None = None,
        destination_level: DestinationLevel | None = None,
        province_code: str | None = None,
        attraction_id: UUID | None = None,
    ) -> V2KnowledgeResult: ...


class RagV2KnowledgeAdapter:
    def __init__(self, *, retrieval: RetrievalService) -> None:
        self._retrieval = retrieval

    def answer(
        self,
        query: str,
        *,
        destination_code: str | None = None,
        destination_level: DestinationLevel | None = None,
        province_code: str | None = None,
        attraction_id: UUID | None = None,
    ) -> V2KnowledgeResult:
        try:
            result = self._retrieval.retrieve(
                query=query,
                dataset_key="rag-v2-production",
                destination_code=destination_code,
                destination_level=destination_level,
                province_code=province_code,
                attraction_id=attraction_id,
            )
        except AppError as error:
            if error.code not in {
                "RAG_V2_EMBEDDING_UNAVAILABLE",
                "RAG_V2_UNAVAILABLE",
            }:
                raise
            return V2KnowledgeResult(
                status="unavailable",
                reply=None,
                evidence=(),
                error_code=error.code,
            )

        evidence = result.evidence
        if not evidence:
            return V2KnowledgeResult(
                status="empty",
                reply=None,
                evidence=(),
                error_code=None,
            )

        if any(
            not item.content
            or not isinstance(item.source_url, str)
            or not item.source_url.startswith("https://")
            or item.source_type
            not in {"official", "government", "trusted_provider"}
            for item in evidence
        ):
            return V2KnowledgeResult(
                status="unavailable",
                reply=None,
                evidence=(),
                error_code="RAG_V2_MALFORMED_RESULT",
            )

        reply = "\n\n".join(item.content for item in evidence)
        return V2KnowledgeResult(
            status="grounded",
            reply=reply,
            evidence=evidence,
        )
