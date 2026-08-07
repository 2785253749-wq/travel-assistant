import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from app.core import http as http_module


def test_request_body_limit_middleware_replays_allowed_chunks():
    middleware_type = getattr(http_module, "RequestBodyLimitMiddleware", None)
    assert middleware_type is not None

    received_body = bytearray()
    sent_messages = []

    async def downstream(scope, receive, send):
        del scope
        while True:
            message = await receive()
            received_body.extend(message.get("body", b""))
            if not message.get("more_body", False):
                break
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    async def exercise():
        incoming = [
            {"type": "http.request", "body": b"safe-", "more_body": True},
            {"type": "http.request", "body": b"body", "more_body": False},
        ]

        async def receive():
            return incoming.pop(0)

        async def send(message):
            sent_messages.append(message)

        await middleware_type(downstream)(
            {
                "type": "http",
                "method": "POST",
                "headers": [],
                "state": {"request_id": "req-safe"},
            },
            receive,
            send,
        )

    asyncio.run(exercise())

    assert bytes(received_body) == b"safe-body"
    assert sent_messages[0]["status"] == 204


def test_request_body_limit_middleware_replays_terminal_disconnect_after_buffering():
    middleware_type = getattr(http_module, "RequestBodyLimitMiddleware", None)
    assert middleware_type is not None

    upstream_calls = 0
    received = []

    async def downstream(scope, receive, send):
        del scope, send
        received.append(await receive())
        received.append(await receive())

    async def exercise():
        nonlocal upstream_calls

        async def receive():
            nonlocal upstream_calls
            upstream_calls += 1
            return {"type": "http.disconnect"}

        async def send(_message):
            return None

        await middleware_type(downstream)(
            {"type": "http", "method": "POST", "headers": [], "state": {}},
            receive,
            send,
        )

    asyncio.run(exercise())

    assert received == [{"type": "http.disconnect"}] * 2
    assert upstream_calls == 1


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("POST", "/api/chat"),
        ("DELETE", "/api/trips/11111111-1111-1111-1111-111111111111"),
        ("GET", "/health"),
    ],
)
def test_request_body_limit_counts_actual_bytes_for_every_method(method, path):
    from app.main import app

    marker = "private-oversized-marker"
    body = json.dumps(
        {
            # Fewer than 64 KiB characters but more than 64 KiB on the wire.
            "message": marker + ("旅" * 22_000),
            "thread_id": "oversized",
            "action": "collect",
        },
        ensure_ascii=False,
    )
    response = TestClient(app).request(
        method,
        path,
        content=body,
        headers={
            "Content-Type": "application/json",
            "Content-Length": "1",
            "X-Request-ID": "req-too-large",
        },
    )

    assert response.status_code == 413
    assert response.headers["X-Request-ID"] == "req-too-large"
    assert response.json() == {
        "detail": {"code": "REQUEST_TOO_LARGE", "message": "Request body is too large"},
        "request_id": "req-too-large",
    }
    assert marker not in response.text
