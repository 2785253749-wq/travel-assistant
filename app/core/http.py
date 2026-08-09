from __future__ import annotations

from collections import deque
import hmac
import ipaddress
import logging
import secrets

from fastapi.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.config import get_settings
from app.core.logging import operational_context
from app.core.rate_limit import request_rate_limiter


MAX_REQUEST_BODY_BYTES = 64 * 1024
_DEVELOPMENT_NETWORK_SECRET = secrets.token_bytes(32)


def _network_signing_secret() -> bytes:
    configured = get_settings().anon_session_signing_secret
    if configured is not None:
        return configured.get_secret_value().encode("utf-8")
    return _DEVELOPMENT_NETWORK_SECRET


def _network_digest(network: str) -> str:
    return hmac.digest(
        _network_signing_secret(),
        b"network-rate-limit-v1\0" + network.encode("ascii"),
        "sha256",
    ).hex()


class ChatNetworkRateLimitMiddleware:
    """Charge the trusted network bucket before any authentication provider call."""

    def __init__(self, app: ASGIApp) -> None:
        self._app = app

    async def __call__(
        self, scope: Scope, receive: Receive, send: Send
    ) -> None:
        if (
            scope["type"] != "http"
            or scope.get("method") != "POST"
            or scope.get("path") != "/api/chat"
        ):
            await self._app(scope, receive, send)
            return

        network = normalized_client_network(scope)
        network_digest = _network_digest(network)
        network_subject = "network-digest:" + network_digest
        state = scope.setdefault("state", {})
        if isinstance(state, dict):
            state["network_subject"] = network_subject
            state["log_subject"] = network_subject
            state["log_intent"] = "not_evaluated"
        if request_rate_limiter.allow(
            (("ip:" + network_digest, get_settings().request_ip_per_minute),)
        ):
            await self._app(scope, receive, send)
            return

        logging.getLogger("app.api.chat").warning(
            "request_rate_limited",
            extra=operational_context(
                subject=network_subject,
                intent="not_evaluated",
                stage="rejected",
                error_code="REQUEST_RATE_LIMITED",
            ),
        )
        request_id = _request_id(scope)
        await JSONResponse(
            status_code=429,
            content={
                "detail": {
                    "code": "REQUEST_RATE_LIMITED",
                    "message": "Too many requests; please retry later",
                },
                "request_id": request_id,
            },
            headers={"X-Request-ID": request_id},
        )(scope, receive, send)


def normalized_client_network(scope: Scope) -> str:
    source = get_settings().trusted_client_ip_header
    if source == "cf-connecting-ip":
        values = [
            value.decode("latin-1")
            for name, value in scope.get("headers", [])
            if name.lower() == b"cf-connecting-ip"
        ]
        if len(values) != 1 or "," in values[0]:
            return "unavailable"
        candidate = values[0].strip()
    else:
        client = scope.get("client")
        candidate = str(client[0]) if client else "unavailable"
    try:
        address = ipaddress.ip_address(candidate)
    except ValueError:
        return "unavailable"
    prefix_length = 32 if address.version == 4 else 64
    return str(ipaddress.ip_network(f"{address}/{prefix_length}", strict=False))


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

        terminal_disconnect_seen = False

        async def replay_receive() -> Message:
            nonlocal terminal_disconnect_seen
            if buffered:
                message = buffered.popleft()
            elif terminal_disconnect_seen:
                return {"type": "http.disconnect"}
            else:
                message = await receive()
            if message["type"] == "http.disconnect":
                terminal_disconnect_seen = True
            return message

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
