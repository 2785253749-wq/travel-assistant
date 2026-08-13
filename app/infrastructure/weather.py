from __future__ import annotations

from datetime import date

import httpx
from postgrest.exceptions import APIError

from app.application.weather import WeatherQuotaUnavailable


class SupabaseWeatherQuotaRepository:
    """Private service-role adapter for the atomic weather quota RPC."""

    def __init__(self, client: object) -> None:
        self._client = client

    def reserve(self, usage_date: date, daily_limit: int) -> bool:
        if daily_limit <= 0:
            raise ValueError("daily_limit must be positive")
        try:
            response = self._client.rpc(
                "reserve_weather_quota",
                {
                    "p_usage_date": usage_date.isoformat(),
                    "p_daily_limit": daily_limit,
                },
            ).execute()
        except (APIError, httpx.HTTPError):
            raise WeatherQuotaUnavailable from None
        data = getattr(response, "data", response)
        if data is True or data is False:
            return data
        if isinstance(data, list) and len(data) == 1 and isinstance(data[0], bool):
            return data[0]
        raise WeatherQuotaUnavailable
