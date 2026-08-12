from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Literal

import httpx

from app.core.config import Settings
from app.providers.base import ProviderResult, utc_now


AMAP_WEATHER_URL = "https://restapi.amap.com/v3/weather/weatherInfo"
AMAP_WEATHER_SOURCE = "https://restapi.amap.com/v3/weather/"
_CHINA_TIMEZONE = timezone(timedelta(hours=8))


@dataclass(frozen=True)
class AmapLiveWeather:
    province: str
    city: str
    adcode: str
    weather: str
    temperature: str
    wind_direction: str
    wind_power: str
    humidity: str
    report_time: datetime


@dataclass(frozen=True)
class AmapForecastCast:
    date: date
    day_weather: str
    night_weather: str
    day_temperature: str
    night_temperature: str


@dataclass(frozen=True)
class AmapForecast:
    province: str
    city: str
    adcode: str
    report_time: datetime
    casts: tuple[AmapForecastCast, ...]


@dataclass(frozen=True)
class AmapWeatherPayload:
    live: AmapLiveWeather | None = None
    forecast: AmapForecast | None = None


class AmapWeatherProvider:
    """Server-only adapter for the AMap weather Web Service API."""

    def __init__(
        self,
        *,
        settings: Settings,
        client: httpx.Client | None = None,
    ) -> None:
        self._key = (
            settings.amap_web_service_key.get_secret_value().strip()
            if settings.amap_web_service_key is not None
            else ""
        )
        self._timeout_seconds = settings.weather_timeout_seconds
        self._client = client or httpx.Client(timeout=self._timeout_seconds)

    def weather(
        self,
        adcode: str,
        extensions: Literal["base", "all"],
    ) -> ProviderResult[AmapWeatherPayload]:
        fetched_at = utc_now()
        if not self._key:
            return _failure(fetched_at, "WEATHER_NOT_CONFIGURED")
        try:
            response = self._client.get(
                AMAP_WEATHER_URL,
                params={
                    "key": self._key,
                    "city": adcode,
                    "extensions": extensions,
                    "output": "JSON",
                },
                headers={"Accept": "application/json"},
                timeout=self._timeout_seconds,
            )
            if response.status_code >= 400:
                return _failure(fetched_at, "WEATHER_HTTP_ERROR")
            payload = response.json()
        except httpx.TimeoutException:
            return _failure(fetched_at, "WEATHER_TIMEOUT")
        except httpx.RequestError:
            return _failure(fetched_at, "WEATHER_NETWORK_ERROR")
        except ValueError:
            return _failure(fetched_at, "WEATHER_INVALID_RESPONSE")

        if not isinstance(payload, dict):
            return _failure(fetched_at, "WEATHER_INVALID_RESPONSE")
        if payload.get("status") != "1" or payload.get("infocode") != "10000":
            return _failure(fetched_at, "WEATHER_PROVIDER_ERROR")
        try:
            parsed = (
                _parse_live(payload)
                if extensions == "base"
                else _parse_forecast(payload)
            )
        except (KeyError, TypeError, ValueError):
            return _failure(fetched_at, "WEATHER_INVALID_RESPONSE")
        if parsed is None:
            return _failure(fetched_at, "WEATHER_INVALID_RESPONSE")
        return ProviderResult(parsed, AMAP_WEATHER_SOURCE, fetched_at)


def _parse_live(payload: dict) -> AmapWeatherPayload:
    lives = payload["lives"]
    if not isinstance(lives, list) or len(lives) != 1:
        raise ValueError("one live weather record required")
    live = lives[0]
    if not isinstance(live, dict):
        raise TypeError("live weather must be an object")
    values = {
        name: _required_string(live, upstream_name)
        for name, upstream_name in (
            ("province", "province"),
            ("city", "city"),
            ("adcode", "adcode"),
            ("weather", "weather"),
            ("temperature", "temperature"),
            ("wind_direction", "winddirection"),
            ("wind_power", "windpower"),
            ("humidity", "humidity"),
        )
    }
    values["report_time"] = _parse_report_time(
        _required_string(live, "reporttime")
    )
    return AmapWeatherPayload(live=AmapLiveWeather(**values))


def _parse_forecast(payload: dict) -> AmapWeatherPayload:
    forecasts = payload["forecasts"]
    if not isinstance(forecasts, list) or len(forecasts) != 1:
        raise ValueError("one forecast record required")
    forecast = forecasts[0]
    if not isinstance(forecast, dict):
        raise TypeError("forecast must be an object")
    raw_casts = forecast.get("casts")
    if not isinstance(raw_casts, list) or not raw_casts:
        raise ValueError("forecast casts required")
    casts = []
    for raw_cast in raw_casts:
        if not isinstance(raw_cast, dict):
            raise TypeError("forecast cast must be an object")
        casts.append(
            AmapForecastCast(
                date=date.fromisoformat(_required_string(raw_cast, "date")),
                day_weather=_required_string(raw_cast, "dayweather"),
                night_weather=_required_string(raw_cast, "nightweather"),
                day_temperature=_required_string(raw_cast, "daytemp"),
                night_temperature=_required_string(raw_cast, "nighttemp"),
            )
        )
    parsed = AmapForecast(
        province=_required_string(forecast, "province"),
        city=_required_string(forecast, "city"),
        adcode=_required_string(forecast, "adcode"),
        report_time=_parse_report_time(_required_string(forecast, "reporttime")),
        casts=tuple(casts),
    )
    return AmapWeatherPayload(forecast=parsed)


def _required_string(payload: dict, name: str) -> str:
    value = payload.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


def _parse_report_time(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%d %H:%M:%S").replace(
        tzinfo=_CHINA_TIMEZONE
    )


def _failure(fetched_at: datetime, error_code: str) -> ProviderResult[AmapWeatherPayload]:
    return ProviderResult(
        None,
        AMAP_WEATHER_SOURCE,
        fetched_at,
        degraded=True,
        error_code=error_code,
    )
