from __future__ import annotations

import argparse
from pathlib import Path
from typing import Protocol, Sequence

import httpx
import yaml
from pydantic import Field

from app.core.config import Settings, get_settings
from app.rag.models import KnowledgeChunk, KnowledgeDocument
from app.rag.repository import KnowledgeStore, KnowledgeRepository


class EmbeddingProvider(Protocol):
    def embed(self, texts: Sequence[str]) -> list[list[float]]: ...


class StoredKnowledgeChunk(KnowledgeChunk):
    """Private import payload; vectors never appear in public RAG contracts."""

    embedding: list[float] = Field(min_length=1024, max_length=1024)


class JinaEmbedder:
    def __init__(self, settings: Settings) -> None:
        if settings.jina_api_key is None:
            raise RuntimeError("Knowledge import requires JINA_API_KEY")
        self._api_key = settings.jina_api_key.get_secret_value()
        self._model = settings.rag_embedding_model

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        response = httpx.post(
            "https://api.jina.ai/v1/embeddings",
            headers={"Authorization": f"Bearer {self._api_key}"},
            json={"model": self._model, "input": list(texts)},
            timeout=30.0,
        )
        response.raise_for_status()
        payload = response.json()
        vectors = [item["embedding"] for item in payload["data"]]
        if len(vectors) != len(texts) or any(len(vector) != 1024 for vector in vectors):
            raise RuntimeError("Jina returned an unexpected embedding shape")
        return vectors


class KnowledgeImportService:
    def __init__(
        self, repository: KnowledgeStore, embedder: EmbeddingProvider, *, chunk_size: int = 1200
    ) -> None:
        if not 1 <= chunk_size <= 6000:
            raise ValueError("chunk_size must be between 1 and 6000")
        self._repository = repository
        self._embedder = embedder
        self._chunk_size = chunk_size

    def import_documents(self, documents: Sequence[KnowledgeDocument]) -> int:
        inserted = 0
        for document in documents:
            chunks = self._chunks_for(document)
            embeddings = self._embedder.embed([chunk.content for chunk in chunks])
            if len(embeddings) != len(chunks):
                raise RuntimeError("Embedder returned a different number of vectors")
            stored_chunks = [
                StoredKnowledgeChunk(**chunk.model_dump(), embedding=embedding)
                for chunk, embedding in zip(chunks, embeddings, strict=True)
            ]
            inserted += self._repository.upsert_document(document, stored_chunks)
        return inserted

    def _chunks_for(self, document: KnowledgeDocument) -> list[KnowledgeChunk]:
        parts = _stable_parts(document.content, self._chunk_size)
        return [
            KnowledgeChunk(
                chunk_id=f"{document.document_id}:{document.document_version}:{index:04d}",
                document_id=document.document_id,
                title=document.title,
                document_version=document.document_version,
                region=document.region,
                topic=document.topic,
                content=part,
                source_label=document.source_label,
                reviewed_on=document.reviewed_on,
            )
            for index, part in enumerate(parts, start=1)
        ]


def _stable_parts(content: str, chunk_size: int) -> list[str]:
    paragraphs = [paragraph.strip() for paragraph in content.replace("\r\n", "\n").split("\n\n")]
    parts: list[str] = []
    for paragraph in filter(None, paragraphs):
        while len(paragraph) > chunk_size:
            parts.append(paragraph[:chunk_size])
            paragraph = paragraph[chunk_size:]
        parts.append(paragraph)
    return parts


def load_documents(content_dir: Path) -> list[KnowledgeDocument]:
    documents: list[KnowledgeDocument] = []
    for path in sorted(content_dir.glob("*.yaml")):
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or not isinstance(payload.get("documents"), list):
            raise ValueError(f"{path} must contain a documents list")
        documents.extend(KnowledgeDocument.model_validate(item) for item in payload["documents"])
    return documents


def main() -> int:
    parser = argparse.ArgumentParser(description="Import private travel knowledge into Supabase")
    parser.add_argument("--content-dir", type=Path, default=Path("app/rag/content"))
    args = parser.parse_args()
    settings = get_settings()
    service = KnowledgeImportService(KnowledgeRepository(settings=settings), JinaEmbedder(settings))
    inserted = service.import_documents(load_documents(args.content_dir))
    print(f"Imported {inserted} new knowledge chunks.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
