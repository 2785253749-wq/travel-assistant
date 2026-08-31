from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from app.agent.graph import SafeTravelAgent
from app.agent.hotel_nearby_query import HotelNearbyQueryExtraction
from app.agent.intent import IntentResult
from app.application.hotel_nearby import (
    HotelNearbyApplicationRequest,
    HotelNearbyApplicationResult,
)
from app.core.errors import AppError
from app.hotels.models import HotelSearchResult, HotelSummary
from app.locations.models import LocationCandidate, ResolvedLocation
from app.locations.service import LocationServiceError


FETCHED_AT = datetime(2026, 8, 31, 8, 0, tzinfo=timezone.utc)


class HotelIntentClassifier:
    def classify(self, _message: str, _has_trip: bool) -> IntentResult:
        return IntentResult(intent="hotel_nearby", confidence=1.0)


@dataclass
class FakeHotelNearbyExtractor:
    extraction: HotelNearbyQueryExtraction

    def extract(self, _message: str) -> HotelNearbyQueryExtraction:
        return self.extraction


@dataclass
class FakeHotelNearbyApplication:
    result: HotelNearbyApplicationResult | None = None
    error: Exception | None = None

    def __post_init__(self) -> None:
        self.requests: list[HotelNearbyApplicationRequest] = []

    def search(self, request: HotelNearbyApplicationRequest) -> HotelNearbyApplicationResult:
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        assert self.result is not None
        return self.result


@dataclass
class FakeHotelNearbyReplyRenderer:
    reply: str = "rendered nearby hotels"

    def __post_init__(self) -> None:
        self.calls: list[tuple[HotelNearbyApplicationResult, int]] = []

    def render(self, result: HotelNearbyApplicationResult, *, radius: int) -> str:
        self.calls.append((result, radius))
        return self.reply


def _application_result() -> HotelNearbyApplicationResult:
    return HotelNearbyApplicationResult(
        location=ResolvedLocation(
            id="location-1",
            name="厦门大学",
            latitude=24.438,
            longitude=118.097,
            provider="fake-location",
        ),
        hotels=HotelSearchResult(
            items=[HotelSummary(id="hotel-1", name="测试酒店", provider="fake-hotel")],
            total=1,
            page=1,
            page_size=10,
            provider="fake-hotel",
            status="success",
            fetched_at=FETCHED_AT,
        ),
    )


def _agent(
    extraction: HotelNearbyQueryExtraction,
    application: FakeHotelNearbyApplication,
    renderer: FakeHotelNearbyReplyRenderer | None = None,
) -> SafeTravelAgent:
    return SafeTravelAgent(
        classifier=HotelIntentClassifier(),
        hotel_nearby_extractor=FakeHotelNearbyExtractor(extraction),
        hotel_nearby_application=application,
        hotel_nearby_renderer=renderer or FakeHotelNearbyReplyRenderer(),
    )


def test_hotel_nearby_happy_path_passes_extracted_request_to_application() -> None:
    application = FakeHotelNearbyApplication(result=_application_result())
    renderer = FakeHotelNearbyReplyRenderer()
    agent = _agent(
        HotelNearbyQueryExtraction(
            location_query="厦门大学",
            city="厦门",
            radius=None,
        ),
        application,
        renderer,
    )

    result = agent.collect("帮我找厦门大学附近的酒店", trip=None)

    assert result.reply == "rendered nearby hotels"
    assert result.intent == "hotel_nearby"
    assert result.error_code is None
    assert application.requests == [
        HotelNearbyApplicationRequest(
            location_query="厦门大学",
            city="厦门",
            radius=2000,
        )
    ]
    assert renderer.calls == [(application.result, 2000)]


def test_run_also_uses_the_hotel_nearby_application_path() -> None:
    application = FakeHotelNearbyApplication(result=_application_result())
    agent = _agent(
        HotelNearbyQueryExtraction("厦门大学", "厦门", 1500),
        application,
    )

    result = agent.run("帮我找厦门大学附近 1.5 公里的酒店", trip=None)

    assert result.reply == "rendered nearby hotels"
    assert application.requests[0].radius == 1500


def test_missing_location_is_reported_without_calling_application() -> None:
    application = FakeHotelNearbyApplication(result=_application_result())
    result = _agent(HotelNearbyQueryExtraction(), application).collect(
        "附近有什么酒店", trip=None
    )

    assert result.error_code == "HOTEL_NEARBY_LOCATION_REQUIRED"
    assert "地点" in result.reply
    assert application.requests == []


def test_missing_city_is_reported_without_calling_application() -> None:
    application = FakeHotelNearbyApplication(result=_application_result())
    result = _agent(
        HotelNearbyQueryExtraction(location_query="厦门大学"), application
    ).collect("厦门大学附近的酒店", trip=None)

    assert result.error_code == "HOTEL_NEARBY_CITY_REQUIRED"
    assert "城市" in result.reply
    assert application.requests == []


def test_invalid_radius_is_reported_without_calling_application() -> None:
    application = FakeHotelNearbyApplication(result=_application_result())
    result = _agent(
        HotelNearbyQueryExtraction(
            location_query="厦门大学",
            city="厦门",
            radius=100,
            invalid_fields=("radius",),
        ),
        application,
    ).collect("厦门大学附近 100 米的酒店", trip=None)

    assert result.error_code == "HOTEL_NEARBY_RADIUS_INVALID"
    assert "范围" in result.reply or "半径" in result.reply
    assert application.requests == []


def test_empty_hotel_result_is_rendered_as_a_successful_lookup() -> None:
    application_result = _application_result()
    application_result.hotels.items = []
    application = FakeHotelNearbyApplication(result=application_result)
    renderer = FakeHotelNearbyReplyRenderer(reply="附近没有找到酒店")

    result = _agent(
        HotelNearbyQueryExtraction("厦门大学", "厦门", 2000),
        application,
        renderer,
    ).collect("厦门大学附近酒店", trip=None)

    assert result.reply == "附近没有找到酒店"
    assert result.error_code is None
    assert len(renderer.calls) == 1


def test_location_not_found_is_mapped_to_a_safe_reply() -> None:
    application = FakeHotelNearbyApplication(
        error=LocationServiceError("LOCATION_NOT_FOUND")
    )

    result = _agent(
        HotelNearbyQueryExtraction("不存在的地点", "厦门", 2000),
        application,
    ).collect("不存在的地点附近酒店", trip=None)

    assert result.error_code == "LOCATION_NOT_FOUND"
    assert "未找到" in result.reply


def test_location_ambiguous_shows_at_most_three_candidates_in_provider_order() -> None:
    candidates = [
        LocationCandidate(id=f"location-{i}", name=f"候选地点{i}", latitude=24 + i / 10, longitude=118, provider="fake")
        for i in range(1, 5)
    ]
    application = FakeHotelNearbyApplication(
        error=LocationServiceError("LOCATION_AMBIGUOUS", candidates=candidates)
    )

    result = _agent(
        HotelNearbyQueryExtraction("候选地点", "厦门", 2000),
        application,
    ).collect("候选地点附近酒店", trip=None)

    assert result.error_code == "LOCATION_AMBIGUOUS"
    assert result.reply.index("候选地点1") < result.reply.index("候选地点2")
    assert result.reply.index("候选地点2") < result.reply.index("候选地点3")
    assert "候选地点4" not in result.reply


def test_provider_error_is_exposed_only_as_stable_code_and_safe_message() -> None:
    application = FakeHotelNearbyApplication(
        error=AppError("BAIDU_HOTEL_TIMEOUT", "secret upstream response")
    )

    result = _agent(
        HotelNearbyQueryExtraction("厦门大学", "厦门", 2000),
        application,
    ).collect("厦门大学附近酒店", trip=None)

    assert result.error_code == "BAIDU_HOTEL_TIMEOUT"
    assert "暂不可用" in result.reply
    assert "secret upstream response" not in result.reply
