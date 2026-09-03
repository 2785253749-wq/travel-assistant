from __future__ import annotations

from math import inf, nan

import httpx
import pytest

from app.core.errors import AppError
from app.rag.embedding import JINA_EMBEDDINGS_URL, RagUnavailable
from app.rag_v2.embedding import JinaQueryEmbedder, JinaQueryProvider


_MODEL = "jina-embeddings-v3"
_TASK = "retrieval.query"
_DIMENSIONS = 1024
_ERROR_CODE = "RAG_V2_EMBEDDING_UNAVAILABLE"
_ERROR_MESSAGE = "RAG V2 embedding is unavailable"
_SECRET_MARKER = "secret-provider-body"


def _embedding_payload(vector: list[object]) -> dict:
    return {
        "data": [{"index": 0, "embedding": vector}],
    }


def _valid_vector() -> list[float]:
    return [0.25] * _DIMENSIONS


class FakeProvider:
    def __init__(self, *, result: object = None, error: Exception | None = None) -> None:
        self.result = result
        self.error = error
        self.calls: list[dict[str, object]] = []

    def embed(
        self,
        query: str,
        *,
        model: str,
        task: str,
        dimensions: int,
    ) -> object:
        self.calls.append(
            {
                "query": query,
                "model": model,
                "task": task,
                "dimensions": dimensions,
            }
        )
        if self.error is not None:
            raise self.error
        return self.result


class FakeResponse:
    def __init__(self, payload: object) -> None:
        self.payload = payload
        self.raise_for_status_calls = 0
        self.json_calls = 0

    def raise_for_status(self) -> None:
        self.raise_for_status_calls += 1

    def json(self) -> object:
        self.json_calls += 1
        return self.payload


class FakeHttpClient:
    def __init__(self, response: FakeResponse) -> None:
        self.response = response
        self.calls: list[dict[str, object]] = []

    def post(self, url: str, **kwargs: object) -> FakeResponse:
        self.calls.append({"url": url, "kwargs": kwargs})
        return self.response


def test_jina_query_provider_sends_fixed_query_request_and_returns_json() -> None:
    query = "  厦门   日落  "
    response = FakeResponse(_embedding_payload(_valid_vector()))
    client = FakeHttpClient(response)
    provider = JinaQueryProvider(
        api_key="server-secret",
        timeout_seconds=7.5,
        client=client,
    )

    payload = provider.embed(
        query,
        model=_MODEL,
        task=_TASK,
        dimensions=_DIMENSIONS,
    )

    assert payload is response.payload
    assert response.raise_for_status_calls == 1
    assert response.json_calls == 1
    assert client.calls == [
        {
            "url": JINA_EMBEDDINGS_URL,
            "kwargs": {
                "headers": {"Authorization": "Bearer server-secret"},
                "json": {
                    "model": _MODEL,
                    "task": _TASK,
                    "dimensions": _DIMENSIONS,
                    "input": [query],
                },
                "timeout": 7.5,
            },
        }
    ]


def test_query_embedder_forwards_already_normalized_query_and_fixed_profile() -> None:
    provider = FakeProvider(result=_embedding_payload(_valid_vector()))

    result = JinaQueryEmbedder(provider=provider).embed_query("厦门   日落")

    assert result == tuple([0.25] * _DIMENSIONS)
    assert provider.calls == [
        {
            "query": "厦门   日落",
            "model": _MODEL,
            "task": _TASK,
            "dimensions": _DIMENSIONS,
        }
    ]


def test_query_embedder_does_not_strip_or_rewrite_provider_input() -> None:
    provider = FakeProvider(result=_embedding_payload(_valid_vector()))

    JinaQueryEmbedder(provider=provider).embed_query("  已归一化  ")

    assert provider.calls[0]["query"] == "  已归一化  "


def test_query_embedder_returns_exactly_1024_finite_float_values() -> None:
    vector = [1, 2.5] + [0.0] * (_DIMENSIONS - 2)
    provider = FakeProvider(result=_embedding_payload(vector))

    result = JinaQueryEmbedder(provider=provider).embed_query("厦门交通")

    assert isinstance(result, tuple)
    assert len(result) == _DIMENSIONS
    assert result[:2] == (1.0, 2.5)
    assert all(isinstance(value, float) for value in result)


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"data": "not-a-list"},
        {"data": []},
        {"data": [{"index": 0, "embedding": _valid_vector()}, {"index": 1}]},
        {"data": [None]},
        {"data": [{"embedding": _valid_vector()}]},
        {"data": [{"index": 0}]},
        {"data": [{"index": 0, "embedding": "not-a-vector"}]},
        _embedding_payload([0.0] * (_DIMENSIONS - 1)),
        _embedding_payload([0.0] * (_DIMENSIONS + 1)),
        _embedding_payload([_SECRET_MARKER] + [0.0] * (_DIMENSIONS - 1)),
        _embedding_payload([True] + [0.0] * (_DIMENSIONS - 1)),
        _embedding_payload([nan] + [0.0] * (_DIMENSIONS - 1)),
        _embedding_payload([inf] + [0.0] * (_DIMENSIONS - 1)),
        _embedding_payload([-inf] + [0.0] * (_DIMENSIONS - 1)),
    ],
)
def test_query_embedder_rejects_malformed_provider_payloads(payload: object) -> None:
    provider = FakeProvider(result=payload)

    with pytest.raises(AppError) as raised:
        JinaQueryEmbedder(provider=provider).embed_query("厦门景点")

    assert raised.value.code == _ERROR_CODE
    assert raised.value.message == _ERROR_MESSAGE
    assert _SECRET_MARKER not in str(raised.value)


@pytest.mark.parametrize(
    "failure",
    [
        RagUnavailable("legacy-provider-failure"),
        TypeError(_SECRET_MARKER),
        ValueError(_SECRET_MARKER),
        httpx.ConnectError(_SECRET_MARKER),
    ],
)
def test_query_embedder_maps_provider_failures_to_frozen_v2_error(
    failure: Exception,
) -> None:
    provider = FakeProvider(error=failure)

    with pytest.raises(AppError) as raised:
        JinaQueryEmbedder(provider=provider).embed_query("厦门景点")

    assert raised.value.code == _ERROR_CODE
    assert raised.value.message == _ERROR_MESSAGE
    assert _SECRET_MARKER not in str(raised.value)


def test_query_embedder_reraises_existing_v2_error_without_replacement() -> None:
    original_error = AppError(_ERROR_CODE, _ERROR_MESSAGE)
    provider = FakeProvider(error=original_error)

    with pytest.raises(AppError) as raised:
        JinaQueryEmbedder(provider=provider).embed_query("厦门景点")

    assert raised.value is original_error


def test_query_embedder_translates_other_provider_app_error() -> None:
    provider = FakeProvider(error=AppError("UPSTREAM_SECRET", _SECRET_MARKER))

    with pytest.raises(AppError) as raised:
        JinaQueryEmbedder(provider=provider).embed_query("厦门景点")

    assert raised.value.code == _ERROR_CODE
    assert raised.value.message == _ERROR_MESSAGE
    assert "UPSTREAM_SECRET" not in str(raised.value)
    assert _SECRET_MARKER not in str(raised.value)
