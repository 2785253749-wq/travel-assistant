from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol, Sequence

from app.rag.embedding import EMBEDDING_DIMENSIONS, RagUnavailable
from app.rag.models import RetrievedChunk


FIXED_RAG_REFUSAL = "资料库没有足够依据，无法可靠回答。"


class Embedder(Protocol):
    def embed(self, texts: list[str]) -> list[list[float]]: ...


class SearchRepository(Protocol):
    def search(
        self, query_vector: Sequence[float], region: str | None, limit: int
    ) -> list[RetrievedChunk]: ...


@dataclass(frozen=True)
class RagAnswer:
    status: Literal["grounded", "refused"]
    reply: str
    chunks: tuple[RetrievedChunk, ...]

    def __post_init__(self) -> None:
        if self.status == "grounded" and not 1 <= len(self.chunks) <= 4:
            raise ValueError("grounded answers require between one and four chunks")
        if self.status == "refused" and (
            self.reply != FIXED_RAG_REFUSAL or self.chunks
        ):
            raise ValueError("refused answers must use the fixed safe response")

    @classmethod
    def refused(cls) -> "RagAnswer":
        return cls(status="refused", reply=FIXED_RAG_REFUSAL, chunks=())

    @classmethod
    def grounded(cls, chunks: Sequence[RetrievedChunk]) -> "RagAnswer":
        selected = tuple(chunks[:4])
        if not selected:
            return cls.refused()
        reply = "\n\n".join(
            f"{chunk.content}\n【来源：{chunk.source_label}】"
            for chunk in selected
        )
        return cls(status="grounded", reply=reply, chunks=selected)


class KnowledgeAnswerService:
    def __init__(
        self,
        repository: SearchRepository,
        embedder: Embedder,
        *,
        threshold: float = 0.7,
    ) -> None:
        if not 0.0 <= threshold <= 1.0:
            raise ValueError("threshold must be between zero and one")
        self._repository = repository
        self._embedder = embedder
        self._threshold = threshold

    def answer(self, question: str, region: str | None = None) -> RagAnswer:
        normalized_question = question.strip()
        if not normalized_question:
            return RagAnswer.refused()
        try:
            embeddings = self._embedder.embed([normalized_question])
            if len(embeddings) != 1 or len(embeddings[0]) != EMBEDDING_DIMENSIONS:
                raise RagUnavailable
            chunks = self._repository.search(embeddings[0], region, limit=4)
        except RagUnavailable:
            return RagAnswer.refused()
        grounded = tuple(
            chunk for chunk in chunks[:4] if chunk.score >= self._threshold
        )
        return RagAnswer.grounded(grounded) if grounded else RagAnswer.refused()


class UnavailableKnowledgeAnswerService:
    def answer(self, question: str, region: str | None = None) -> RagAnswer:
        del question, region
        return RagAnswer.refused()
