from __future__ import annotations

from collections import deque

from fastapi.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send


MAX_REQUEST_BODY_BYTES = 64 * 1024


class RequestBodyLimitMiddleware:
    """Bound and replay each HTTP body at the ASGI receive seam."""

    def __init__(
        self, app: ASGIApp, max_body_bytes: int = MAX_REQUEST_BODY_BYTES
    ) -> None:
        self._app = app
        self._max_body_bytes = max_body_bytes

    async def __call__(
        self, scope: Scope, receive: Receive, send: Send
    ) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        content_length = _content_length(scope)
        if content_length is not None and content_length > self._max_body_bytes:
            await _too_large(_request_id(scope))(scope, receive, send)
            return

        buffered: deque[Message] = deque()
        byte_count = 0
        while True:
            message = await receive()
            buffered.append(message)
            if message["type"] == "http.disconnect":
                break
            if message["type"] != "http.request":
                continue
            byte_count += len(message.get("body", b""))
            if byte_count > self._max_body_bytes:
                await _too_large(_request_id(scope))(scope, receive, send)
                return
            if not message.get("more_body", False):
                break

        async def replay_receive() -> Message:
            if buffered:
                return buffered.popleft()
            return await receive()

        await self._app(scope, replay_receive, send)


def _content_length(scope: Scope) -> int | None:
    values = [
        value
        for name, value in scope.get("headers", [])
        if name.lower() == b"content-length"
    ]
    if len(values) != 1:
        return None
    try:
        value = int(values[0])
    except ValueError:
        return None
    return value if value >= 0 else None


def _request_id(scope: Scope) -> str:
    state = scope.get("state")
    if isinstance(state, dict):
        request_id = state.get("request_id")
        if isinstance(request_id, str):
            return request_id
    return "unavailable"


def _too_large(request_id: str) -> JSONResponse:
    return JSONResponse(
        status_code=413,
        content={
            "detail": {
                "code": "REQUEST_TOO_LARGE",
                "message": "Request body is too large",
            },
            "request_id": request_id,
        },
        headers={"X-Request-ID": request_id},
    )
