from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
import secrets
import sys

import pytest


TODAY = date(2026, 8, 12)


def test_weather_quota_reserves_once_across_two_service_instances() -> None:
    from app.application.weather import WeatherService
    from app.providers.amap_weather import AmapLiveWeather, AmapWeatherPayload
    from app.providers.base import ProviderResult

    class Provider:
        def __init__(self) -> None:
            self.calls = 0

        def weather(self, _adcode, _extensions):
            self.calls += 1
            live = AmapLiveWeather(
                province="福建",
                city="厦门市",
                adcode="350200",
                weather="晴",
                temperature="30",
                wind_direction="东",
                wind_power="3",
                humidity="60",
                report_time=datetime(2026, 8, 12, tzinfo=timezone(timedelta(hours=8))),
            )
            return ProviderResult(
                AmapWeatherPayload(live=live), "test", live.report_time
            )

    class SharedQuota:
        def __init__(self) -> None:
            self.used = 0

        def reserve(self, usage_date, daily_limit):
            assert usage_date == TODAY
            if self.used >= daily_limit:
                return False
            self.used += 1
            return True

    provider = Provider()
    quota = SharedQuota()
    first = WeatherService(
        provider=provider,
        cache_ttl_seconds=60,
        daily_limit=1,
        today=lambda: TODAY,
        quota=quota,
    )
    second = WeatherService(
        provider=provider,
        cache_ttl_seconds=60,
        daily_limit=1,
        today=lambda: TODAY,
        quota=quota,
    )

    assert first.city_card("xiamen").status == "available"
    assert second.city_card("xiamen").status == "unavailable"
    assert quota.used == 1
    assert provider.calls == 1


def test_weather_quota_repository_calls_the_private_atomic_reservation_rpc() -> None:
    from app.infrastructure.weather import SupabaseWeatherQuotaRepository

    calls = []

    class Client:
        def rpc(self, name, params):
            calls.append((name, params))
            return type("Request", (), {"execute": lambda self: type("Response", (), {"data": True})()})()

    result = SupabaseWeatherQuotaRepository(Client()).reserve(TODAY, 100)

    assert result is True
    assert calls == [
        (
            "reserve_weather_quota",
            {"p_usage_date": "2026-08-12", "p_daily_limit": 100},
        )
    ]


def test_weather_quota_repository_fails_closed_on_an_invalid_database_response() -> None:
    from app.application.weather import WeatherQuotaUnavailable
    from app.infrastructure.weather import SupabaseWeatherQuotaRepository

    class Client:
        def rpc(self, _name, _params):
            return type("Request", (), {"execute": lambda self: type("Response", (), {"data": {"allowed": True}})()})()

    with pytest.raises(WeatherQuotaUnavailable):
        SupabaseWeatherQuotaRepository(Client()).reserve(TODAY, 100)


def test_production_weather_wiring_uses_a_service_role_quota_repository(monkeypatch) -> None:
    from app import composition
    from app.core.config import get_settings

    calls = []
    client = object()
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setenv("SUPABASE_URL", "https://project.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "anon-key")
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "service-role-key")
    monkeypatch.setenv("ANON_SESSION_SIGNING_SECRET", secrets.token_urlsafe(32))
    monkeypatch.setenv("AMAP_WEB_SERVICE_KEY", "weather-service-key")
    monkeypatch.setitem(
        sys.modules,
        "supabase",
        SimpleNamespace(create_client=lambda url, key: calls.append((url, key)) or client),
    )
    get_settings.cache_clear()

    try:
        weather = composition.build_weather_service()
    finally:
        get_settings.cache_clear()

    assert weather._quota._client is client
    assert calls == [("https://project.supabase.co/", "service-role-key")]
