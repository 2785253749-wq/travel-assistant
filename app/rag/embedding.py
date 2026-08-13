from __future__ import annotations

import logging
from json import JSONDecodeError
from math import isfinite
from typing import Protocol

import httpx
from pydantic import SecretStr

from app.core.logging import operational_context

JINA_EMBEDDINGS_URL = "https://api.jina.ai/v1/embeddings"
EMBEDDING_DIMENSIONS = 1024


class RagUnavailable(RuntimeError):
    """Internal signal that retrieval cannot produce trustworthy evidence."""


class EmbeddingHttpClient(Protocol):
    def post(self, url: str, **kwargs) -> httpx.Response: ...


class EmbeddingQuota(Protocol):
    def reserve(self, requested: int, limit: int) -> bool: ...


class NoopEmbeddingQuota:
    """Explicitly unlimited quota for the operator-only knowledge import path."""

    def reserve(self, requested: int, limit: int) -> bool:
        del requested, limit
        return True


class JinaEmbeddingTransport:
    """Shared Jina wire adapter and payload validator for runtime and import."""

    def __init__(
        self,
        *,
        api_key: str | SecretStr,
        model: str,
        timeout_seconds: float,
        client: EmbeddingHttpClient | None = None,
    ) -> None:
        key = api_key.get_secret_value() if isinstance(api_key, SecretStr) else api_key
        if not key.strip():
            raise ValueError("api_key must be configured")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self._api_key = key
        self._model = model
        self._timeout_seconds = timeout_seconds
        self._client = client or httpx.Client()

    def embed(self, texts: list[str]) -> list[list[float]]:
        try:
            response = self._client.post(
                JINA_EMBEDDINGS_URL,
                headers={"Authorization": f"Bearer {self._api_key}"},
                json={"model": self._model, "input": texts},
                timeout=self._timeout_seconds,
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            _log_embedding_failure(
                error_code="RAG_EMBEDDING_UNAVAILABLE",
                exception=exc,
                provider_status=getattr(getattr(exc, "response", None), "status_code", None),
            )
            raise RagUnavailable from None
        try:
            payload = response.json()
        except (JSONDecodeError, UnicodeDecodeError, ValueError) as exc:
            _log_embedding_failure(
                error_code="RAG_EMBEDDING_INVALID_RESPONSE",
                exception=exc,
                provider_status=getattr(response, "status_code", None),
            )
            raise RagUnavailable from None
        try:
            return _validated_embeddings(payload, expected_count=len(texts))
        except (OverflowError, RagUnavailable) as exc:
            _log_embedding_failure(
                error_code="RAG_EMBEDDING_INVALID_RESPONSE",
                exception=exc,
                provider_status=getattr(response, "status_code", None),
            )
            raise RagUnavailable from None


class JinaEmbedder:
    def __init__(
        self,
        *,
        api_key: str | SecretStr,
        model: str,
        timeout_seconds: float,
        daily_limit: int,
        quota: EmbeddingQuota,
        client: EmbeddingHttpClient | None = None,
    ) -> None:
        if daily_limit <= 0:
            raise ValueError("daily_limit must be positive")
        self._daily_limit = daily_limit
        self._quota = quota
        self._transport = JinaEmbeddingTransport(
            api_key=api_key,
            model=model,
            timeout_seconds=timeout_seconds,
            client=client,
        )

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        if any(not isinstance(text, str) or not text.strip() for text in texts):
            raise RagUnavailable
        if not self._quota.reserve(len(texts), self._daily_limit):
            logging.getLogger("app.rag").warning(
                "rag_quota_rejected",
                extra=operational_context(
                    provider="jina",
                    error_code="RAG_EMBEDDING_QUOTA_REJECTED",
                    failure_stage="embedding_quota",
                ),
            )
            raise RagUnavailable
        return self._transport.embed(texts)


def _log_embedding_failure(
    *, error_code: str, exception: Exception, provider_status: int | None
) -> None:
    fields = operational_context(
        provider="jina",
        error_code=error_code,
        exception_type=type(exception).__name__,
        failure_stage="embedding",
    )
    if isinstance(provider_status, int):
        fields["provider_status"] = provider_status
    logging.getLogger("app.rag").warning("rag_provider_failure", extra=fields)


def _validated_embeddings(payload: object, *, expected_count: int) -> list[list[float]]:
    if not isinstance(payload, dict):
        raise RagUnavailable
    data = payload.get("data")
    if not isinstance(data, list) or len(data) != expected_count:
        raise RagUnavailable

    vectors: list[list[float]] = []
    for expected_index, item in enumerate(data):
        if not isinstance(item, dict) or item.get("index") != expected_index:
            raise RagUnavailable
        vector = item.get("embedding")
        if not isinstance(vector, list) or len(vector) != EMBEDDING_DIMENSIONS:
            raise RagUnavailable
        if any(
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not isfinite(value)
            for value in vector
        ):
            raise RagUnavailable
        vectors.append([float(value) for value in vector])
    return vectors
