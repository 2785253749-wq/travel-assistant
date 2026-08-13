from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Literal, Protocol, Sequence

from app.rag.embedding import EMBEDDING_DIMENSIONS, RagUnavailable
from app.rag.models import RetrievedChunk


FIXED_RAG_REFUSAL = "资料库没有足够依据，无法可靠回答。"
_PILOT_REGIONS = frozenset({"福建", "云南", "厦门"})
_UNSUPPORTED_STATIC_CORPUS_REQUEST = re.compile(
    r"(?:此刻|今天|最新|最便宜|精确|绝对|保证|支付|没有来源|"
    r"忽略资料库|断言|临时闭馆|还有票|实时(?:预订|空位|最低|排队|票价))"
)


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
        normalized_region = region.strip() if isinstance(region, str) else ""
        if (
            not normalized_question
            or normalized_region not in _PILOT_REGIONS
            or _UNSUPPORTED_STATIC_CORPUS_REQUEST.search(normalized_question)
        ):
            return RagAnswer.refused()
        try:
            embeddings = self._embedder.embed([normalized_question])
            if len(embeddings) != 1 or len(embeddings[0]) != EMBEDDING_DIMENSIONS:
                raise RagUnavailable
            chunks = self._repository.search(embeddings[0], normalized_region, limit=4)
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
