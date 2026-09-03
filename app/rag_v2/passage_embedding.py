from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Protocol

import httpx
from pydantic import SecretStr

from app.core.errors import AppError
from app.rag.embedding import JINA_EMBEDDINGS_URL, RagUnavailable


_MODEL = "jina-embeddings-v3"
_TASK = "retrieval.passage"
_DIMENSIONS = 1024

_ERROR_CODE = "RAG_V2_EMBEDDING_UNAVAILABLE"
_ERROR_MESSAGE = "RAG V2 embedding is unavailable"


class PassageEmbeddingProvider(Protocol):
    def embed(
        self,
        text: str,
        *,
        model: str,
        task: str,
        dimensions: int,
    ) -> object: ...


class _HttpResponse(Protocol):
    def raise_for_status(self) -> None: ...

    def json(self) -> object: ...


class _HttpClient(Protocol):
    def post(self, url: str, **kwargs: object) -> _HttpResponse: ...


class PassageEmbedder(Protocol):
    def embed_passage(self, text: str) -> tuple[float, ...]: ...


class JinaPassageProvider:
    """Thin Jina wire adapter for one passage embedding request."""

    def __init__(
        self,
        *,
        api_key: str | SecretStr,
        timeout_seconds: float,
        client: _HttpClient | None = None,
    ) -> None:
        key = api_key.get_secret_value() if isinstance(api_key, SecretStr) else api_key
        if not key.strip():
            raise ValueError("api_key must be configured")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self._api_key = key
        self._timeout_seconds = timeout_seconds
        self._client = client or httpx.Client()

    def embed(
        self,
        text: str,
        *,
        model: str,
        task: str,
        dimensions: int,
    ) -> object:
        response = self._client.post(
            JINA_EMBEDDINGS_URL,
            headers={"Authorization": f"Bearer {self._api_key}"},
            json={
                "model": model,
                "task": task,
                "dimensions": dimensions,
                "input": [text],
            },
            timeout=self._timeout_seconds,
        )
        response.raise_for_status()
        return response.json()


class JinaPassageEmbedder:
    def __init__(self, *, provider: PassageEmbeddingProvider) -> None:
        self._provider = provider

    def embed_passage(self, text: str) -> tuple[float, ...]:
        try:
            payload = self._provider.embed(
                text,
                model=_MODEL,
                task=_TASK,
                dimensions=_DIMENSIONS,
            )
            return _validated_passage_embedding(payload)
        except AppError as exc:
            if exc.code == _ERROR_CODE:
                raise
            raise _unavailable_error() from None
        except RagUnavailable:
            raise _unavailable_error() from None
        except Exception:
            raise _unavailable_error() from None


def _validated_passage_embedding(payload: object) -> tuple[float, ...]:
    if not isinstance(payload, Mapping):
        raise ValueError("embedding response must be an object")

    data = payload.get("data")
    if not isinstance(data, list) or len(data) != 1:
        raise ValueError("embedding response data must contain one item")

    item = data[0]
    if not isinstance(item, Mapping) or item.get("index") != 0:
        raise ValueError("embedding response item is invalid")

    vector = item.get("embedding")
    if not isinstance(vector, list) or len(vector) != _DIMENSIONS:
        raise ValueError("embedding vector must contain 1024 values")

    normalized: list[float] = []
    for value in vector:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("embedding vector contains a non-numeric value")
        converted = float(value)
        if not math.isfinite(converted):
            raise ValueError("embedding vector contains a non-finite value")
        normalized.append(converted)
    return tuple(normalized)


def _unavailable_error() -> AppError:
    return AppError(_ERROR_CODE, _ERROR_MESSAGE)
