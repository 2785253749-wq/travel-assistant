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


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class DurationTransport(httpx.BaseTransport):
    """Simulate request durations while honoring the supplied HTTPX budget."""

    def __init__(self, clock: FakeClock, events: list[tuple[float, httpx.Response]]) -> None:
        self._clock = clock
        self._events = iter(events)
        self.requests: list[httpx.Request] = []

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        duration, response = next(self._events)
        timeout = request.extensions["timeout"]
        request_budget = max(value for value in timeout.values() if value is not None)
        if duration > request_budget:
            self._clock.advance(request_budget)
            raise httpx.ReadTimeout("operation budget exhausted", request=request)
        self._clock.advance(duration)
        return response


def json_response(payload: dict, status_code: int = 200) -> httpx.Response:
    return httpx.Response(status_code, content=json.dumps(payload).encode("utf-8"))
