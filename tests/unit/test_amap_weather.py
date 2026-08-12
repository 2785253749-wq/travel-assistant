from datetime import datetime
from urllib.parse import parse_qs, urlparse

import httpx

from app.core.config import Settings
from app.providers.amap_weather import AmapWeatherProvider
from tests.fixtures.providers import RecordingTransport, json_response


def weather_settings(**overrides) -> Settings:
    values = {
        "amap_web_service_key": "web-service-secret",
        "amap_js_key": "browser-public-key",
        "weather_timeout_seconds": 2.5,
        "_env_file": None,
    }
    values.update(overrides)
    return Settings(**values)


def live_payload() -> dict:
    return {
        "status": "1",
        "count": "1",
        "info": "OK",
        "infocode": "10000",
        "lives": [
            {
                "province": "福建",
                "city": "厦门市",
                "adcode": "350200",
                "weather": "多云",
                "temperature": "31",
                "winddirection": "南",
                "windpower": "≤3",
                "humidity": "75",
                "reporttime": "2026-08-12 14:30:00",
            }
        ],
    }


def test_weather_request_uses_server_key_adcode_extension_and_configured_timeout() -> None:
    transport = RecordingTransport([json_response(live_payload())])
    provider = AmapWeatherProvider(
        settings=weather_settings(),
        client=httpx.Client(transport=transport),
    )

    result = provider.weather("350200", "base")

    query = parse_qs(urlparse(str(transport.requests[0].url)).query)
    assert query == {
        "key": ["web-service-secret"],
        "city": ["350200"],
        "extensions": ["base"],
        "output": ["JSON"],
    }
    assert transport.requests[0].extensions["timeout"]["read"] == 2.5
    assert result.degraded is False


def test_weather_parses_live_report_time_into_a_timezone_aware_value() -> None:
    transport = RecordingTransport([json_response(live_payload())])
    provider = AmapWeatherProvider(
        settings=weather_settings(),
        client=httpx.Client(transport=transport),
    )

    result = provider.weather("350200", "base")

    assert result.data is not None
    assert result.data.live is not None
    assert result.data.live.city == "厦门市"
    assert result.data.live.report_time == datetime.fromisoformat(
        "2026-08-12T14:30:00+08:00"
    )


def test_missing_web_service_key_never_falls_back_to_browser_key_or_http() -> None:
    transport = RecordingTransport([])
    provider = AmapWeatherProvider(
        settings=weather_settings(amap_web_service_key=None),
        client=httpx.Client(transport=transport),
    )

    result = provider.weather("350200", "base")

    assert result.data is None
    assert result.degraded is True
    assert result.error_code == "WEATHER_NOT_CONFIGURED"
    assert transport.requests == []


def test_amap_non_success_response_is_a_degraded_result() -> None:
    transport = RecordingTransport(
        [json_response({"status": "0", "info": "INVALID_USER_KEY", "infocode": "10001"})]
    )
    provider = AmapWeatherProvider(
        settings=weather_settings(),
        client=httpx.Client(transport=transport),
    )

    result = provider.weather("350200", "base")

    assert result.data is None
    assert result.degraded is True
    assert result.error_code == "WEATHER_PROVIDER_ERROR"


def test_weather_timeout_is_a_degraded_result() -> None:
    transport = RecordingTransport([httpx.ReadTimeout("timed out")])
    provider = AmapWeatherProvider(
        settings=weather_settings(),
        client=httpx.Client(transport=transport),
    )

    result = provider.weather("350200", "base")

    assert result.data is None
    assert result.degraded is True
    assert result.error_code == "WEATHER_TIMEOUT"


def test_malformed_live_payload_is_a_degraded_result() -> None:
    payload = live_payload()
    payload["lives"][0]["reporttime"] = "not-a-report-time"
    transport = RecordingTransport([json_response(payload)])
    provider = AmapWeatherProvider(
        settings=weather_settings(),
        client=httpx.Client(transport=transport),
    )

    result = provider.weather("350200", "base")

    assert result.data is None
    assert result.degraded is True
    assert result.error_code == "WEATHER_INVALID_RESPONSE"


def test_http_error_is_a_degraded_result_without_upstream_text() -> None:
    transport = RecordingTransport([json_response({}, status_code=401)])
    provider = AmapWeatherProvider(
        settings=weather_settings(),
        client=httpx.Client(transport=transport),
    )

    result = provider.weather("350200", "base")

    assert result.data is None
    assert result.degraded is True
    assert result.error_code == "WEATHER_HTTP_ERROR"
    assert "401" not in result.error_code


def test_weather_parses_forecast_report_time_and_cast_dates() -> None:
    payload = {
        "status": "1",
        "count": "1",
        "info": "OK",
        "infocode": "10000",
        "forecasts": [
            {
                "province": "福建",
                "city": "厦门市",
                "adcode": "350200",
                "reporttime": "2026-08-12 14:30:00",
                "casts": [
                    {
                        "date": "2026-08-14",
                        "week": "5",
                        "dayweather": "阵雨",
                        "nightweather": "阴",
                        "daytemp": "31",
                        "nighttemp": "25",
                        "daywind": "南",
                        "nightwind": "南",
                        "daypower": "≤3",
                        "nightpower": "≤3",
                    }
                ],
            }
        ],
    }
    transport = RecordingTransport([json_response(payload)])
    provider = AmapWeatherProvider(
        settings=weather_settings(),
        client=httpx.Client(transport=transport),
    )

    result = provider.weather("350200", "all")

    assert result.data is not None
    assert result.data.forecast is not None
    assert result.data.forecast.report_time == datetime.fromisoformat(
        "2026-08-12T14:30:00+08:00"
    )
    assert result.data.forecast.casts[0].date.isoformat() == "2026-08-14"
    assert result.data.forecast.casts[0].day_weather == "阵雨"
