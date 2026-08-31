from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass, field
from datetime import datetime, timezone
from time import monotonic
from typing import Callable, Generic, TypeVar

import httpx

from app.agent.graph import TrustedEvidence


T = TypeVar("T")
R = TypeVar("R")

USER_AGENT = "TravelAssistantMVP/1.0 (+https://github.com/travel-assistant)"
HTTP_TIMEOUT = httpx.Timeout(6.0, connect=3.0)
OPERATION_TIMEOUT_SECONDS = 6.0


class UpstreamHttpError(Exception):
    pass


class UpstreamPayloadError(Exception):
    pass


@dataclass(frozen=True)
class OperationDeadline:
    expires_at: float
    clock: Callable[[], float]

    @classmethod
    def start(cls, clock: Callable[[], float] = monotonic) -> "OperationDeadline":
        return cls(clock() + OPERATION_TIMEOUT_SECONDS, clock)

    def httpx_timeout(self) -> httpx.Timeout:
        remaining = self.expires_at - self.clock()
        if remaining <= 0:
            raise httpx.TimeoutException("Provider operation deadline exceeded")
        return httpx.Timeout(remaining, connect=min(3.0, remaining))

    def raise_if_expired(self) -> None:
        if self.clock() >= self.expires_at:
            raise httpx.TimeoutException("Provider operation deadline exceeded")

    def run(self, operation: Callable[[], R]) -> R:
        """Bound a blocking HTTPX call by the operation's wall-clock remainder."""
        self.raise_if_expired()
        executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="provider-http")
        future = executor.submit(operation)
        remaining = self.expires_at - self.clock()
        try:
            if remaining <= 0:
                raise FutureTimeoutError
            return future.result(timeout=remaining)
        except FutureTimeoutError:
            future.cancel()
            raise httpx.TimeoutException("Provider operation deadline exceeded") from None
        finally:
            # The HTTPX phase timeout also uses the remaining budget, so an
            # abandoned worker is bounded even though Python cannot kill it.
            executor.shutdown(wait=False, cancel_futures=True)


@dataclass(frozen=True)
class ProviderResult(Generic[T]):
    data: T | None
    source: str
    fetched_at: datetime
    degraded: bool = False
    error_code: str | None = None
    evidence: tuple[TrustedEvidence, ...] = field(default_factory=tuple)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def request_json(
    client: httpx.Client,
    url: str,
    params: dict[str, str],
    deadline: OperationDeadline | None = None,
    request_slot: Callable[[], AbstractContextManager[None]] | None = None,
) -> dict:
    """Fetch JSON with explicit timeouts and exactly one transient retry."""
    operation_deadline = deadline or OperationDeadline.start()
    for attempt in range(2):
        try:
            with request_slot() if request_slot is not None else nullcontext():
                request_timeout = operation_deadline.httpx_timeout()
                response = operation_deadline.run(
                    lambda: client.get(
                        url,
                        params=params,
                        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
                        timeout=request_timeout,
                    )
                )
        except httpx.TimeoutException:
            raise
        except httpx.RequestError:
            if attempt == 0:
                continue
            raise
        operation_deadline.raise_if_expired()
        if response.status_code >= 500 and attempt == 0:
            continue
        if response.status_code >= 400:
            raise UpstreamHttpError(str(response.status_code))
        try:
            payload = response.json()
        except ValueError:
            raise UpstreamPayloadError from None
        if not isinstance(payload, dict):
            raise UpstreamPayloadError
        return payload
