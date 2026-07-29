from datetime import date
from urllib.parse import parse_qs, urlparse

import httpx

from app.schemas import TravelProfile
from app.providers.booking_links import BookingLinkBuilder
from app.providers.free_weather import WeatherProvider
from app.providers.places import PlacesProvider
from tests.fixtures.providers import RecordingTransport, json_response


def test_place_search_rewrites_once_after_empty_result() -> None:
    transport = RecordingTransport([
        json_response({"features": []}),
        json_response({"features": [{"properties": {"name": "西湖", "city": "杭州"}}]}),
    ])
    provider = PlacesProvider(client=httpx.Client(transport=transport))

    result = provider.search(city="杭州", query="西湖景区")

    assert result.data[0].name == "西湖"
    assert parse_qs(urlparse(str(transport.requests[1].url)).query)["q"] == ["杭州 西湖"]
    assert result.degraded is False


def test_weather_timeout_returns_degraded_result() -> None:
    transport = RecordingTransport([httpx.TimeoutException("timed out")])
    provider = WeatherProvider(client=httpx.Client(transport=transport))

    result = provider.forecast("杭州", date(2026, 8, 1), date(2026, 8, 3))

    assert result.data is None
    assert result.degraded is True
    assert result.error_code == "WEATHER_TIMEOUT"


def test_provider_retries_a_server_failure_once_and_keeps_trusted_metadata() -> None:
    transport = RecordingTransport([
        json_response({}, status_code=503),
        json_response({"results": [{"latitude": 30.2741, "longitude": 120.1551}]}),
        json_response({"daily": {"temperature_2m_max": [31, 32], "temperature_2m_min": [24, 25]}}),
    ])
    provider = WeatherProvider(client=httpx.Client(transport=transport))

    result = provider.forecast("杭州", date(2026, 8, 1), date(2026, 8, 2))

    assert len(transport.requests) == 3
    assert result.degraded is False
    assert result.source.startswith("https://")
    assert result.evidence[0].source_type == "trusted_provider"
    assert result.evidence[0].source_url == result.source


def test_weather_uses_provider_geocoding_for_the_requested_destination() -> None:
    transport = RecordingTransport([
        json_response({"results": [{"latitude": 31.2304, "longitude": 121.4737}]}),
        json_response({"daily": {"temperature_2m_max": [34], "temperature_2m_min": [26]}}),
    ])
    provider = WeatherProvider(client=httpx.Client(transport=transport))

    result = provider.forecast("上海", date(2026, 8, 1), date(2026, 8, 1))

    assert result.data is not None
    assert parse_qs(urlparse(str(transport.requests[0].url)).query)["name"] == ["上海"]
    assert parse_qs(urlparse(str(transport.requests[1].url)).query)["latitude"] == ["31.2304"]


def test_booking_links_are_allowlisted_encoded_search_jumps_with_disclaimer() -> None:
    profile = TravelProfile(
        origin="上海?x=1", destination="杭州&redirect=https://evil.example",
        start_date="2026-08-01", end_date="2026-08-03", travelers=2,
    )

    links = BookingLinkBuilder().build(profile)

    assert links.disclaimer == "价格和库存以第三方平台为准；链接仅用于搜索跳转，不代表已确认的价格或库存。"
    for url in (links.train, links.hotel, links.flight):
        parsed = urlparse(url)
        assert parsed.scheme == "https"
        assert parsed.hostname in {"www.12306.cn", "www.ctrip.com"}
        assert "evil.example" not in parsed.hostname
    assert "%3F" in links.train
    assert "%26redirect%3Dhttps%3A%2F%2Fevil.example" in links.hotel


def test_places_failure_does_not_invent_places() -> None:
    transport = RecordingTransport([httpx.ConnectError("offline"), httpx.ConnectError("offline")])
    provider = PlacesProvider(client=httpx.Client(transport=transport))

    result = provider.search(city="杭州", query="西湖")

    assert result.data == []
    assert result.degraded is True
    assert result.error_code == "PLACES_NETWORK_ERROR"
