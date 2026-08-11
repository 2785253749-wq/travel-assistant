from datetime import date

import httpx
import pytest

from app.rag.embedding import JinaEmbedder, RagUnavailable


def _embedding_payload(*vectors: list[float]) -> dict:
    return {
        "object": "list",
        "data": [
            {"object": "embedding", "index": index, "embedding": vector}
            for index, vector in enumerate(vectors)
        ],
        "model": "jina-embeddings-v3",
        "usage": {"total_tokens": 2},
    }


def test_jina_uses_bearer_auth_configured_timeout_and_model() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=_embedding_payload([0.25] * 1024))

    client = httpx.Client(transport=httpx.MockTransport(handler))
    embedder = JinaEmbedder(
        api_key="server-secret",
        model="jina-embeddings-v3",
        timeout_seconds=7.5,
        daily_limit=10,
        client=client,
    )

    assert embedder.embed(["厦门交通"])[0][:2] == [0.25, 0.25]

    assert len(requests) == 1
    request = requests[0]
    assert request.url == "https://api.jina.ai/v1/embeddings"
    assert request.headers["Authorization"] == "Bearer server-secret"
    assert request.extensions["timeout"] == {
        "connect": 7.5,
        "read": 7.5,
        "write": 7.5,
        "pool": 7.5,
    }
    assert request.read().decode("utf-8") == (
        '{"model":"jina-embeddings-v3","input":["厦门交通"]}'
    )


def test_daily_embedding_quota_refuses_before_an_extra_http_request() -> None:
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json=_embedding_payload([0.0] * 1024))

    embedder = JinaEmbedder(
        api_key="server-secret",
        model="jina-embeddings-v3",
        timeout_seconds=3.0,
        daily_limit=1,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        today=lambda: date(2026, 8, 12),
    )

    embedder.embed(["first"])
    with pytest.raises(RagUnavailable):
        embedder.embed(["second"])

    assert calls == 1


def test_daily_quota_does_not_reset_when_clock_moves_backwards() -> None:
    calls = 0
    dates = iter(
        [
            date(2026, 8, 13),
            date(2026, 8, 13),
            date(2026, 8, 12),
        ]
    )

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json=_embedding_payload([0.0] * 1024))

    embedder = JinaEmbedder(
        api_key="server-secret",
        model="jina-embeddings-v3",
        timeout_seconds=3.0,
        daily_limit=1,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        today=lambda: next(dates),
    )

    embedder.embed(["first"])
    with pytest.raises(RagUnavailable):
        embedder.embed(["stale-clock"])

    assert calls == 1


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"data": "not-a-list"},
        {"data": [{"index": 0, "embedding": "not-a-vector"}]},
        _embedding_payload([0.0] * 3),
        _embedding_payload([0.0] * 1024, [0.0] * 1024),
        _embedding_payload([float("nan")] * 1024),
    ],
)
def test_bad_or_wrong_dimension_payload_is_unavailable(payload: dict) -> None:
    embedder = JinaEmbedder(
        api_key="server-secret",
        model="jina-embeddings-v3",
        timeout_seconds=3.0,
        daily_limit=10,
        client=httpx.Client(
            transport=httpx.MockTransport(
                lambda _request: httpx.Response(200, json=payload)
            )
        ),
    )

    with pytest.raises(RagUnavailable):
        embedder.embed(["云南雨季"])


@pytest.mark.parametrize(
    "outcome",
    [
        httpx.ConnectError("offline"),
        httpx.Response(503, json={"error": "unavailable"}),
    ],
)
def test_upstream_failure_is_unavailable(outcome) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if isinstance(outcome, Exception):
            raise outcome
        outcome.request = request
        return outcome

    embedder = JinaEmbedder(
        api_key="server-secret",
        model="jina-embeddings-v3",
        timeout_seconds=3.0,
        daily_limit=10,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    with pytest.raises(RagUnavailable):
        embedder.embed(["福建交通"])
