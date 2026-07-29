from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Generic, TypeVar

import httpx

from app.agent.graph import TrustedEvidence


T = TypeVar("T")

USER_AGENT = "TravelAssistantMVP/1.0 (+https://github.com/travel-assistant)"
HTTP_TIMEOUT = httpx.Timeout(6.0, connect=3.0)


class UpstreamHttpError(Exception):
    pass


class UpstreamPayloadError(Exception):
    pass


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
) -> dict | None:
    """Fetch JSON with explicit timeouts and exactly one transient retry."""
    for attempt in range(2):
        try:
            response = client.get(
                url,
                params=params,
                headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
                timeout=HTTP_TIMEOUT,
            )
        except httpx.TimeoutException:
            raise
        except httpx.RequestError:
            if attempt == 0:
                continue
            raise
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
