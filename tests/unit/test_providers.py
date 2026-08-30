from datetime import date, datetime, timezone
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event, Lock
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from app.schemas import TravelProfile
from app.providers.aggregate import ProviderEvidenceAggregator
from app.providers.base import ProviderResult
from app.providers.booking_links import BookingLinkBuilder, _search_url
from app.providers.free_weather import WeatherProvider, WeatherSummary
from app.providers.places import Place, PlacesProvider
from tests.fixtures.providers import DurationTransport, FakeClock, RecordingTransport, json_response


class VersionedWeather:
    def __init__(self) -> None:
        self.calls = 0

    def forecast(self, destination: str, start: date, end: date):
        self.calls += 1
        return ProviderResult(
            WeatherSummary(
                destination,
                start,
                end,
                (30.0 + self.calls,),
                (20.0 + self.calls,),
            ),
            "weather-fixture",
            datetime(2026, 8, 1, self.calls, tzinfo=timezone.utc),
        )


class VersionedPlaces:
    def __init__(self) -> None:
        self.calls = 0

    def search(self, city: str, _query: str):
        self.calls += 1
        return ProviderResult(
            [Place(f"景点-{self.calls}", city)],
            "places-fixture",
            datetime(2026, 8, 1, self.calls, tzinfo=timezone.utc),
        )


def test_aggregator_starts_weather_and_places_before_either_finishes() -> None:
    weather_started = Event()
    places_started = Event()
    fetched_at = datetime(2026, 8, 1, tzinfo=timezone.utc)

    class CoordinatedWeather:
        def forecast(self, destination: str, start: date, end: date):
            weather_started.set()
            if not places_started.wait(timeout=1):
                raise RuntimeError("places provider did not start concurrently")
            return ProviderResult(
                WeatherSummary(destination, start, end, (30.0,), (22.0,)),
                "weather-fixture",
                fetched_at,
            )

    class CoordinatedPlaces:
        def search(self, city: str, _query: str):
            places_started.set()
            if not weather_started.wait(timeout=1):
                raise RuntimeError("weather provider did not start concurrently")
            return ProviderResult(
                [Place("西湖", city)],
                "places-fixture",
                fetched_at,
            )

    profile = TravelProfile(
        origin="上海",
        destination="杭州",
        start_date="2026-08-01",
        end_date="2026-08-03",
        travelers=2,
        preferences=["西湖"],
    )

    bundle = ProviderEvidenceAggregator(
        weather=CoordinatedWeather(),
        places=CoordinatedPlaces(),
    ).fetch(profile)

    assert tuple(result.source for result in bundle.results) == (
        "weather-fixture",
        "places-fixture",
    )
    assert bundle.warnings == ()


def test_same_profile_uses_provider_cache_only_within_short_ttl() -> None:
    clock = FakeClock()
    weather = VersionedWeather()
    places = VersionedPlaces()
    aggregator = ProviderEvidenceAggregator(
        weather=weather,
        places=places,
        cache_ttl_seconds=30.0,
        clock=clock,
    )
    profile = TravelProfile(
        origin="上海",
        destination="杭州",
        start_date="2026-08-01",
        end_date="2026-08-03",
        travelers=2,
        preferences=["西湖"],
    )

    first = aggregator.fetch(profile)
    clock.advance(29.0)
    cached = aggregator.fetch(profile.model_copy(deep=True))

    assert cached.results == first.results
    assert cached.booking_links == first.booking_links
    assert (weather.calls, places.calls) == (1, 1)

    clock.advance(1.0)
    refreshed = aggregator.fetch(profile)

    assert refreshed.results != first.results
    assert refreshed.booking_links == first.booking_links
    assert (weather.calls, places.calls) == (2, 2)


def test_provider_cache_has_a_bounded_lru_capacity() -> None:
    weather = VersionedWeather()
    places = VersionedPlaces()
    aggregator = ProviderEvidenceAggregator(
        weather=weather,
        places=places,
        cache_max_entries=2,
    )
    profile = TravelProfile(
        origin="origin",
        destination="destination-a",
        start_date="2026-08-01",
        end_date="2026-08-03",
        travelers=2,
    )

    aggregator.fetch(profile)
    aggregator.fetch(profile.model_copy(update={"destination": "destination-b"}))
    aggregator.fetch(profile.model_copy(update={"destination": "destination-c"}))
    aggregator.fetch(profile)

    assert (weather.calls, places.calls) == (4, 4)
    assert len(aggregator._cache) == 2


def test_same_profile_concurrent_cache_miss_is_single_flight() -> None:
    entrants = Barrier(2)
    release_provider = Event()
    calls_lock = Lock()
    weather = VersionedWeather()
    places = VersionedPlaces()
    original_forecast = weather.forecast
    original_search = places.search

    def blocking_forecast(*args):
        with calls_lock:
            result = original_forecast(*args)
        assert release_provider.wait(timeout=2)
        return result

    def blocking_search(*args):
        with calls_lock:
            result = original_search(*args)
        assert release_provider.wait(timeout=2)
        return result

    weather.forecast = blocking_forecast
    places.search = blocking_search
    aggregator = ProviderEvidenceAggregator(weather=weather, places=places)
    profile = TravelProfile(
        origin="origin",
        destination="same-destination",
        start_date="2026-08-01",
        end_date="2026-08-03",
        travelers=2,
    )

    def fetch():
        entrants.wait(timeout=2)
        return aggregator.fetch(profile)

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(fetch) for _ in range(2)]
        assert all(not future.done() for future in futures)
        release_provider.set()
        bundles = [future.result(timeout=2) for future in futures]

    assert (weather.calls, places.calls) == (1, 1)
    assert bundles[0] is bundles[1]


def test_chat_composition_reuses_the_provider_aggregator_between_requests() -> None:
    from app import composition

    provider_dependency = getattr(composition, "get_provider_evidence_aggregator", None)
    if provider_dependency is not None:
        provider_dependency.cache_clear()
    try:
        first_application = composition.build_chat_application(None)
        second_application = composition.build_chat_application(None)

        first_provider = first_application._agent_factory(TravelProfile())._evidence_provider
        second_provider = second_application._agent_factory(TravelProfile())._evidence_provider

        assert first_provider is second_provider
    finally:
        if provider_dependency is not None:
            provider_dependency.cache_clear()


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


def test_places_second_empty_result_is_a_safe_degradation_after_one_rewrite() -> None:
    transport = RecordingTransport([
        json_response({"features": []}),
        json_response({"features": []}),
    ])
    provider = PlacesProvider(client=httpx.Client(transport=transport))

    result = provider.search(city="杭州", query="西湖景区")

    assert result.data == []
    assert result.degraded is True
    assert result.error_code == "PLACES_EMPTY_AFTER_RETRY"
    assert len(transport.requests) == 2
    assert parse_qs(urlparse(str(transport.requests[1].url)).query)["q"] == ["杭州 西湖"]


def test_places_filters_obvious_business_locations_from_travel_evidence() -> None:
    transport = RecordingTransport([
        json_response(
            {
                "features": [
                    {"properties": {"name": "第一三共制药（上海）", "city": "上海"}},
                    {"properties": {"name": "昌硕科技（上海）有限公司", "city": "上海"}},
                    {"properties": {"name": "上海博物馆", "city": "上海"}},
                ]
            }
        )
    ])
    provider = PlacesProvider(client=httpx.Client(transport=transport))

    result = provider.search(city="上海", query="上海景点")

    assert [place.name for place in result.data] == ["上海博物馆"]
    assert all("公司" not in evidence.fact and "制药" not in evidence.fact for evidence in result.evidence)


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


def test_weather_retries_trial_city_geocoding_with_an_english_alias() -> None:
    transport = RecordingTransport([
        json_response({"results": []}),
        json_response({"results": [{"latitude": 24.4798, "longitude": 118.0894}]}),
        json_response({"daily": {"temperature_2m_max": [32], "temperature_2m_min": [26]}}),
    ])
    provider = WeatherProvider(client=httpx.Client(transport=transport))

    result = provider.forecast("厦门", date(2026, 8, 1), date(2026, 8, 1))

    assert result.data is not None
    assert [
        parse_qs(urlparse(str(request.url)).query)["name"]
        for request in transport.requests[:2]
    ] == [["厦门"], ["Xiamen"]]


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


@pytest.mark.parametrize("payload", [
    {"unexpected": []},
    {"features": {"not": "a list"}},
])
def test_places_malformed_schema_is_invalid_instead_of_empty_rewrite(payload: dict) -> None:
    transport = RecordingTransport([json_response(payload)])
    provider = PlacesProvider(client=httpx.Client(transport=transport))

    result = provider.search(city="杭州", query="西湖")

    assert result.data == []
    assert result.degraded is True
    assert result.error_code == "PLACES_INVALID_RESPONSE"
    assert len(transport.requests) == 1


def test_places_malformed_feature_is_invalid_instead_of_ignored() -> None:
    transport = RecordingTransport([json_response({"features": [{"properties": "invalid"}]})])
    provider = PlacesProvider(client=httpx.Client(transport=transport))

    result = provider.search(city="杭州", query="西湖")

    assert result.data == []
    assert result.degraded is True
    assert result.error_code == "PLACES_INVALID_RESPONSE"
    assert len(transport.requests) == 1


def test_places_rejects_feature_fields_outside_properties_without_rewrite() -> None:
    transport = RecordingTransport([
        json_response({"features": [{"name": "西湖", "city": "杭州"}]})
    ])
    provider = PlacesProvider(client=httpx.Client(transport=transport))

    result = provider.search(city="杭州", query="西湖")

    assert result.data == []
    assert result.degraded is True
    assert result.error_code == "PLACES_INVALID_RESPONSE"
    assert len(transport.requests) == 1


@pytest.mark.parametrize("base_url", [
    "https://user@www.12306.cn/",
    "https://www.12306.cn:444/",
    "https://www.12306.cn:443/",
])
def test_booking_search_url_rejects_userinfo_and_every_explicit_port(base_url: str) -> None:
    with pytest.raises(ValueError, match="allowlisted"):
        _search_url(base_url, {"destination": "杭州"})


def test_places_retry_shares_one_six_second_operation_deadline() -> None:
    clock = FakeClock()
    transport = DurationTransport(clock, [
        (4.0, json_response({}, status_code=503)),
        (3.0, json_response({"features": [{"properties": {"name": "西湖", "city": "杭州"}}]})),
    ])
    provider = PlacesProvider(client=httpx.Client(transport=transport), clock=clock)

    result = provider.search(city="杭州", query="西湖")

    assert result.data == []
    assert result.degraded is True
    assert result.error_code == "PLACES_TIMEOUT"
    assert clock.now == 6.0
    assert transport.requests[1].extensions["timeout"]["read"] == 2.0
