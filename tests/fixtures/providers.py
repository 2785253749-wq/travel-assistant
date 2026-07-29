from __future__ import annotations

import json

import httpx


class RecordingTransport(httpx.BaseTransport):
    """A deterministic in-memory HTTPX transport for provider tests."""

    def __init__(self, responses: list[httpx.Response | Exception]) -> None:
        self._responses = iter(responses)
        self.requests: list[httpx.Request] = []

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        next_response = next(self._responses)
        if isinstance(next_response, Exception):
            raise next_response
        return next_response


def json_response(payload: dict, status_code: int = 200) -> httpx.Response:
    return httpx.Response(status_code, content=json.dumps(payload).encode("utf-8"))
