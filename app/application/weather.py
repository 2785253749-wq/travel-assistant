from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from threading import Lock
from time import monotonic
from typing import Callable, Literal, Protocol

from app.providers.amap_weather import AmapWeatherPayload
from app.providers.base import ProviderResult
from app.schemas import ItineraryWeather, WeatherCard


_PILOT_CITIES = {
    "xiamen": ("350200", "厦门"),
    "fujian": ("350000", "福建"),
    "yunnan": ("530000", "云南"),
}
_DESTINATION_IDS = {
    "xiamen": "xiamen",
    "厦门": "xiamen",
    "厦门市": "xiamen",
    "fujian": "fujian",
    "福建": "fujian",
    "福建省": "fujian",
    "yunnan": "yunnan",
    "云南": "yunnan",
    "云南省": "yunnan",
}
_CHINA_TIMEZONE = timezone(timedelta(hours=8))
_UNAVAILABLE_SUMMARY = "天气信息暂不可用"


class WeatherProvider(Protocol):
    def weather(
        self,
        adcode: str,
        extensions: Literal["base", "all"],
    ) -> ProviderResult[AmapWeatherPayload]: ...


class WeatherQuotaUnavailable(Exception):
    """The authoritative server-side weather quota cannot be safely checked."""


class WeatherQuota(Protocol):
    def reserve(self, usage_date: date, daily_limit: int) -> bool: ...


class WeatherService:
    def __init__(
        self,
        *,
        provider: WeatherProvider,
        cache_ttl_seconds: float,
        daily_limit: int,
        clock: Callable[[], float] = monotonic,
        today: Callable[[], date] | None = None,
        quota: WeatherQuota | None = None,
    ) -> None:
        self._provider = provider
        self._cache_ttl_seconds = cache_ttl_seconds
        self._daily_limit = daily_limit
        self._clock = clock
        self._today = today or _china_today
        self._quota = quota
        self._cache: dict[
            tuple[str, Literal["base", "all"]],
            tuple[float, ProviderResult[AmapWeatherPayload]],
        ] = {}
        self._quota_day: date | None = None
        self._calls_today = 0
        self._lock = Lock()

    def city_card(self, city_id: str) -> WeatherCard:
        city = _pilot_city(city_id)
        if city is None:
            return _unavailable_card(city_id)
        adcode, label = city
        result = self._fetch(adcode, "base")
        if result is None or result.degraded or result.data is None:
            return _unavailable_card(label)
        live = result.data.live
        if live is None:
            return _unavailable_card(label)
        return WeatherCard(
            city=live.city,
            status="available",
            summary=(
                f"{live.weather}，{live.temperature}°C，湿度 {live.humidity}%，"
                f"{live.wind_direction}风 {live.wind_power}级"
            ),
            report_time=live.report_time,
        )

    def daily_weather(
        self,
        destination: str,
        travel_date: date,
    ) -> ItineraryWeather | None:
        city = _pilot_city(destination)
        if city is None:
            return None
        adcode, _ = city
        result = self._fetch(adcode, "all")
        if result is None or result.degraded or result.data is None:
            return None
        forecast = result.data.forecast
        if forecast is None:
            return None
        report_day = forecast.report_time.astimezone(_CHINA_TIMEZONE).date()
        if not report_day <= travel_date <= report_day + timedelta(days=2):
            return None
        cast = next((item for item in forecast.casts if item.date == travel_date), None)
        if cast is None:
            return None
        condition = (
            cast.day_weather
            if cast.day_weather == cast.night_weather
            else f"{cast.day_weather}转{cast.night_weather}"
        )
        return ItineraryWeather(
            city=forecast.city,
            status="available",
            summary=(
                f"{condition}，{cast.night_temperature}–{cast.day_temperature}°C"
            ),
            report_time=forecast.report_time,
            date=travel_date,
        )

    def _fetch(
        self,
        adcode: str,
        extensions: Literal["base", "all"],
    ) -> ProviderResult[AmapWeatherPayload] | None:
        cache_key = (adcode, extensions)
        with self._lock:
            now = self._clock()
            cached = self._cache.get(cache_key)
            if cached is not None and now < cached[0]:
                return cached[1]
            if cached is not None:
                del self._cache[cache_key]

            current_day = self._today()
            if current_day != self._quota_day:
                self._quota_day = current_day
                self._calls_today = 0
            if not self._reserve_quota(current_day):
                return None
            result = self._provider.weather(adcode, extensions)
            self._cache[cache_key] = (now + self._cache_ttl_seconds, result)
            return result

    def _reserve_quota(self, current_day: date) -> bool:
        if self._quota is not None:
            try:
                return self._quota.reserve(current_day, self._daily_limit)
            except WeatherQuotaUnavailable:
                return False
        if self._calls_today >= self._daily_limit:
            return False
        self._calls_today += 1
        return True


def _pilot_city(value: str) -> tuple[str, str] | None:
    city_id = _DESTINATION_IDS.get(value.strip().casefold())
    return _PILOT_CITIES.get(city_id) if city_id is not None else None


def _unavailable_card(city: str) -> WeatherCard:
    return WeatherCard(
        city=city.strip() or "未知城市",
        status="unavailable",
        summary=_UNAVAILABLE_SUMMARY,
    )


def _china_today() -> date:
    return datetime.now(_CHINA_TIMEZONE).date()
