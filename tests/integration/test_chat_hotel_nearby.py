from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app.application.hotel_nearby import (
    HotelNearbyApplicationRequest,
    HotelNearbyApplicationResult,
)
from app.hotels.models import HotelSearchResult, HotelSummary
from app.locations.models import ResolvedLocation
from app.locations.models import LocationCandidate
from app.locations.service import LocationServiceError


FETCHED_AT = datetime(2026, 8, 31, 8, 0, tzinfo=timezone.utc)


def _application_result(*, empty: bool = False) -> HotelNearbyApplicationResult:
    hotels = [] if empty else [
        HotelSummary(id=f"hotel-{index}", name=f"测试酒店{index}", provider="baidu")
        for index in range(1, 5)
    ]
    return HotelNearbyApplicationResult(
        location=ResolvedLocation(
            id="location-synthetic",
            name="厦门大学",
            latitude=24.44,
            longitude=118.09,
            provider="baidu",
        ),
        hotels=HotelSearchResult(
            items=hotels,
            total=len(hotels),
            page=1,
            page_size=10,
            provider="baidu",
            status="success",
            fetched_at=FETCHED_AT,
        ),
    )


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


@pytest.fixture
def fake_hotel_nearby_application(monkeypatch: pytest.MonkeyPatch):
    from app import composition

    fake = FakeHotelNearbyApplication(result=_application_result())
    monkeypatch.setattr(composition, "get_hotel_nearby_application", lambda: fake)
    return fake


def test_chat_api_runs_real_hotel_nearby_chain_with_fake_application(
    client: TestClient,
    fake_hotel_nearby_application: FakeHotelNearbyApplication,
) -> None:
    from app.schemas import ChatResponse

    response = client.post(
        "/api/chat",
        json={"message": "帮我找厦门大学附近的酒店", "thread_id": "hotel-http-happy"},
    )

    assert response.status_code == 200
    payload = response.json()
    ChatResponse.model_validate(payload)
    assert payload["stage"] == "collecting"
    assert "厦门大学" in payload["reply"]
    assert "测试酒店1" in payload["reply"]
    assert "测试酒店2" in payload["reply"]
    assert "测试酒店3" in payload["reply"]
    assert "测试酒店4" not in payload["reply"]
    assert len(fake_hotel_nearby_application.requests) == 1
    request = fake_hotel_nearby_application.requests[0]
    assert request.location_query == "厦门大学"
    assert request.city == "厦门"
    assert request.radius == 2000


def test_chat_api_keeps_empty_hotel_result_as_normal_business_response(
    client: TestClient,
    fake_hotel_nearby_application: FakeHotelNearbyApplication,
) -> None:
    fake_hotel_nearby_application.result = _application_result(empty=True)

    response = client.post(
        "/api/chat",
        json={"message": "帮我找厦门大学附近的酒店", "thread_id": "hotel-http-empty"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert "未找到" in payload["reply"]
    assert payload.get("error_code") is None
    assert len(fake_hotel_nearby_application.requests) == 1


def test_chat_api_missing_city_clarifies_without_calling_application(
    client: TestClient,
    fake_hotel_nearby_application: FakeHotelNearbyApplication,
) -> None:
    response = client.post(
        "/api/chat",
        json={
            "message": "鼓浪屿附近酒店",
            "thread_id": "hotel-http-city",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert "城市" in payload["reply"] or "完整地点" in payload["reply"]
    assert payload["error_code"] == "HOTEL_NEARBY_CITY_REQUIRED"
    assert fake_hotel_nearby_application.requests == []


def test_chat_api_uses_unsupported_for_hotel_transit_question(
    client: TestClient,
    fake_hotel_nearby_application: FakeHotelNearbyApplication,
) -> None:
    response = client.post(
        "/api/chat",
        json={
            "message": "我订的酒店附近有地铁吗",
            "thread_id": "hotel-http-transit",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["error_code"] == "OUT_OF_SCOPE"
    assert "请补充目的地城市，我才能查询试点旅行资料" not in payload["reply"]
    assert fake_hotel_nearby_application.requests == []


def test_chat_api_maps_location_not_found_without_exposing_exception(
    client: TestClient,
    fake_hotel_nearby_application: FakeHotelNearbyApplication,
) -> None:
    fake_hotel_nearby_application.error = LocationServiceError("LOCATION_NOT_FOUND")

    response = client.post(
        "/api/chat",
        json={"message": "帮我找厦门大学附近的酒店", "thread_id": "hotel-http-not-found"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["error_code"] == "LOCATION_NOT_FOUND"
    assert "未找到" in payload["reply"]
    assert "LocationServiceError" not in response.text
    assert len(fake_hotel_nearby_application.requests) == 1


def test_chat_api_maps_ambiguous_location_with_three_ordered_candidates(
    client: TestClient,
    fake_hotel_nearby_application: FakeHotelNearbyApplication,
) -> None:
    candidates = [
        LocationCandidate(
            id=f"synthetic-location-{index}",
            name=f"候选地点{index}",
            latitude=24.4 + index / 100,
            longitude=118.09,
            provider="fake",
        )
        for index in range(1, 5)
    ]
    fake_hotel_nearby_application.error = LocationServiceError(
        "LOCATION_AMBIGUOUS", candidates=candidates
    )

    response = client.post(
        "/api/chat",
        json={"message": "帮我找厦门大学附近的酒店", "thread_id": "hotel-http-ambiguous"},
    )

    assert response.status_code == 200
    payload = response.json()
    reply = payload["reply"]
    assert payload["error_code"] == "LOCATION_AMBIGUOUS"
    assert "多个地点" in reply
    assert reply.index("候选地点1") < reply.index("候选地点2")
    assert reply.index("候选地点2") < reply.index("候选地点3")
    assert "候选地点4" not in reply
    assert "synthetic-location-1" not in reply
    assert len(fake_hotel_nearby_application.requests) == 1
