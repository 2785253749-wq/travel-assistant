from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol
from uuid import UUID

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
        result = self._retrieval.retrieve(
            query=query,
            dataset_key="rag-v2-production",
            destination_code=destination_code,
            destination_level=destination_level,
            province_code=province_code,
            attraction_id=attraction_id,
        )
        evidence = result.evidence
        reply = "\n\n".join(item.content for item in evidence)
        return V2KnowledgeResult(
            status="grounded",
            reply=reply,
            evidence=evidence,
        )
