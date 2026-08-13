from datetime import date, datetime, timedelta, timezone

import pytest

from app.application.weather import WeatherService
from app.providers.amap_weather import (
    AMAP_WEATHER_SOURCE,
    AmapForecast,
    AmapForecastCast,
    AmapLiveWeather,
    AmapWeatherPayload,
)
from app.providers.base import ProviderResult
from tests.fixtures.providers import FakeClock


REPORT_TIME = datetime(2026, 8, 12, 14, 30, tzinfo=timezone(timedelta(hours=8)))


class FakeWeatherProvider:
    def __init__(self, responses):
        self._responses = iter(responses)
        self.calls = []

    def weather(self, adcode, extensions):
        self.calls.append((adcode, extensions))
        response = next(self._responses)
        if isinstance(response, Exception):
            raise response
        return response


class MutableToday:
    def __init__(self, value: date) -> None:
        self.value = value

    def __call__(self) -> date:
        return self.value


def live_result(temperature: str = "31") -> ProviderResult[AmapWeatherPayload]:
    live = AmapLiveWeather(
        province="福建",
        city="厦门市",
        adcode="350200",
        weather="多云",
        temperature=temperature,
        wind_direction="南",
        wind_power="≤3",
        humidity="75",
        report_time=REPORT_TIME,
    )
    return ProviderResult(
        AmapWeatherPayload(live=live),
        AMAP_WEATHER_SOURCE,
        REPORT_TIME,
    )


def forecast_result() -> ProviderResult[AmapWeatherPayload]:
    forecast = AmapForecast(
        province="福建",
        city="厦门市",
        adcode="350200",
        report_time=REPORT_TIME,
        casts=(
            AmapForecastCast(
                date=date(2026, 8, 13),
                day_weather="晴",
                night_weather="多云",
                day_temperature="33",
                night_temperature="26",
            ),
            AmapForecastCast(
                date=date(2026, 8, 14),
                day_weather="阵雨",
                night_weather="阴",
                day_temperature="31",
                night_temperature="25",
            ),
        ),
    )
    return ProviderResult(
        AmapWeatherPayload(forecast=forecast),
        AMAP_WEATHER_SOURCE,
        REPORT_TIME,
    )


def forecast_result_with_fourth_day() -> ProviderResult[AmapWeatherPayload]:
    result = forecast_result()
    assert result.data is not None
    assert result.data.forecast is not None
    forecast = result.data.forecast
    return ProviderResult(
        AmapWeatherPayload(
            forecast=AmapForecast(
                province=forecast.province,
                city=forecast.city,
                adcode=forecast.adcode,
                report_time=forecast.report_time,
                casts=forecast.casts
                + (
                    AmapForecastCast(
                        date=date(2026, 8, 15),
                        day_weather="晴",
                        night_weather="晴",
                        day_temperature="34",
                        night_temperature="27",
                    ),
                ),
            )
        ),
        AMAP_WEATHER_SOURCE,
        REPORT_TIME,
    )


def unavailable_result(code: str = "WEATHER_TIMEOUT") -> ProviderResult[AmapWeatherPayload]:
    return ProviderResult(
        None,
        AMAP_WEATHER_SOURCE,
        REPORT_TIME,
        degraded=True,
        error_code=code,
    )


def service(provider, **overrides) -> WeatherService:
    values = {
        "provider": provider,
        "cache_ttl_seconds": 60,
        "daily_limit": 10,
    }
    values.update(overrides)
    return WeatherService(**values)


def test_city_card_maps_xiamen_to_adcode_and_builds_public_summary() -> None:
    provider = FakeWeatherProvider([live_result()])

    card = service(provider).city_card("xiamen")

    assert provider.calls == [("350200", "base")]
    assert card.model_dump(mode="json") == {
        "city": "厦门市",
        "status": "available",
        "summary": "多云，31°C，湿度 75%，南风 ≤3级",
        "report_time": "2026-08-12T14:30:00+08:00",
    }


@pytest.mark.parametrize(
    ("city_id", "expected_adcode"),
    [
        ("fuzhou", "350100"),
        ("dali", "532900"),
        ("lijiang", "530700"),
    ],
)
def test_city_card_maps_every_visible_trial_city_to_its_city_adcode(
    city_id: str, expected_adcode: str
) -> None:
    provider = FakeWeatherProvider([live_result()])

    card = service(provider).city_card(city_id)

    assert card.status == "available"
    assert provider.calls == [(expected_adcode, "base")]


def test_city_card_uses_ttl_cache_then_refreshes_at_expiry() -> None:
    clock = FakeClock()
    provider = FakeWeatherProvider([live_result("31"), live_result("32")])
    weather = service(provider, clock=clock)

    first = weather.city_card("xiamen")
    clock.advance(59)
    cached = weather.city_card("xiamen")
    clock.advance(1)
    refreshed = weather.city_card("xiamen")

    assert first.summary == cached.summary == "多云，31°C，湿度 75%，南风 ≤3级"
    assert refreshed.summary == "多云，32°C，湿度 75%，南风 ≤3级"
    assert provider.calls == [("350200", "base"), ("350200", "base")]


def test_daily_limit_blocks_new_upstream_calls_and_resets_next_day() -> None:
    today = MutableToday(date(2026, 8, 12))
    provider = FakeWeatherProvider([live_result(), live_result()])
    weather = service(provider, daily_limit=1, today=today)

    first = weather.city_card("xiamen")
    blocked = weather.city_card("fujian")
    today.value = date(2026, 8, 13)
    resumed = weather.city_card("fujian")

    assert first.status == "available"
    assert blocked.status == "unavailable"
    assert resumed.status == "available"
    assert provider.calls == [("350200", "base"), ("350000", "base")]


def test_daily_weather_selects_the_requested_forecast_date() -> None:
    provider = FakeWeatherProvider([forecast_result()])

    weather = service(provider).daily_weather("厦门", date(2026, 8, 14))

    assert provider.calls == [("350200", "all")]
    assert weather is not None
    assert weather.model_dump(mode="json") == {
        "city": "厦门市",
        "status": "available",
        "summary": "阵雨转阴，25–31°C",
        "report_time": "2026-08-12T14:30:00+08:00",
        "date": "2026-08-14",
    }


def test_daily_weather_rejects_a_fourth_day_even_if_provider_payload_contains_it() -> None:
    provider = FakeWeatherProvider([forecast_result_with_fourth_day()])

    weather = service(provider).daily_weather("厦门", date(2026, 8, 15))

    assert weather is None
    assert provider.calls == [("350200", "all")]


def test_provider_degraded_result_becomes_an_unavailable_card() -> None:
    degraded = service(FakeWeatherProvider([unavailable_result()])).city_card("xiamen")

    assert degraded.model_dump(mode="json") == {
        "city": "厦门",
        "status": "unavailable",
        "summary": "天气信息暂不可用",
        "report_time": None,
    }


def test_programming_exception_from_provider_is_not_disguised_as_weather_unavailable() -> None:
    with pytest.raises(RuntimeError, match="programming defect"):
        service(FakeWeatherProvider([RuntimeError("programming defect")])).city_card(
            "xiamen"
        )


def test_unsupported_pilot_destination_never_calls_provider() -> None:
    provider = FakeWeatherProvider([])
    weather = service(provider)

    card = weather.city_card("beijing")
    forecast = weather.daily_weather("北京", date(2026, 8, 14))

    assert card.status == "unavailable"
    assert forecast is None
    assert provider.calls == []
