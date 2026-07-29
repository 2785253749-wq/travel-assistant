from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from hashlib import sha256
from time import monotonic
from typing import Callable

import httpx

from app.agent.graph import TrustedEvidence
from app.providers.base import (
    ProviderResult,
    HTTP_TIMEOUT,
    OperationDeadline,
    UpstreamHttpError,
    UpstreamPayloadError,
    request_json,
    utc_now,
)


WEATHER_SOURCE = "https://api.open-meteo.com/v1/forecast"
GEOCODING_SOURCE = "https://geocoding-api.open-meteo.com/v1/search"


@dataclass(frozen=True)
class WeatherSummary:
    destination: str
    start: date
    end: date
    maximum_celsius: tuple[float, ...]
    minimum_celsius: tuple[float, ...]


class WeatherProvider:
    """Free Open-Meteo adapter; location resolution remains intentionally separate."""

    def __init__(
        self,
        client: httpx.Client | None = None,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        self._client = client or httpx.Client(timeout=HTTP_TIMEOUT)
        self._clock = clock

    def forecast(self, destination: str, start: date, end: date) -> ProviderResult[WeatherSummary]:
        fetched_at = utc_now()
        deadline = OperationDeadline.start(self._clock)
        try:
            coordinates = request_json(
                self._client,
                GEOCODING_SOURCE,
                {"name": destination.strip(), "count": "1", "language": "zh", "format": "json"},
                deadline,
            )
            results = coordinates.get("results")
            candidate = results[0] if isinstance(results, list) and results else None
            latitude = candidate.get("latitude") if isinstance(candidate, dict) else None
            longitude = candidate.get("longitude") if isinstance(candidate, dict) else None
            if not isinstance(latitude, (float, int)) or not isinstance(longitude, (float, int)):
                return ProviderResult(None, WEATHER_SOURCE, fetched_at, True, "WEATHER_LOCATION_NOT_FOUND")
            payload = request_json(
                self._client,
                WEATHER_SOURCE,
                {
                    "latitude": str(latitude),
                    "longitude": str(longitude),
                    "daily": "temperature_2m_max,temperature_2m_min",
                    "timezone": "Asia/Shanghai",
                    "start_date": start.isoformat(),
                    "end_date": end.isoformat(),
                },
                deadline,
            )
        except httpx.TimeoutException:
            return ProviderResult(None, WEATHER_SOURCE, fetched_at, True, "WEATHER_TIMEOUT")
        except httpx.RequestError:
            return ProviderResult(None, WEATHER_SOURCE, fetched_at, True, "WEATHER_NETWORK_ERROR")
        except UpstreamHttpError:
            return ProviderResult(None, WEATHER_SOURCE, fetched_at, True, "WEATHER_HTTP_ERROR")
        except UpstreamPayloadError:
            return ProviderResult(None, WEATHER_SOURCE, fetched_at, True, "WEATHER_INVALID_RESPONSE")

        daily = payload.get("daily") if payload else None
        maximum = daily.get("temperature_2m_max") if isinstance(daily, dict) else None
        minimum = daily.get("temperature_2m_min") if isinstance(daily, dict) else None
        if not isinstance(maximum, list) or not isinstance(minimum, list):
            return ProviderResult(None, WEATHER_SOURCE, fetched_at, True, "WEATHER_INVALID_RESPONSE")

        summary = WeatherSummary(destination, start, end, tuple(maximum), tuple(minimum))
        fact = (
            f"{destination} {start.isoformat()} 至 {end.isoformat()} 的最高气温为 "
            f"{', '.join(map(str, maximum))}°C，最低气温为 {', '.join(map(str, minimum))}°C。"
        )
        evidence = TrustedEvidence(
            evidence_id=f"weather-{sha256(fact.encode('utf-8')).hexdigest()[:16]}",
            fact=fact,
            source_url=WEATHER_SOURCE,
            source_type="trusted_provider",
        )
        return ProviderResult(summary, WEATHER_SOURCE, fetched_at, evidence=(evidence,))
