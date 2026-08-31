from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

import pytest

from app.core.errors import AppError
from app.hotels.models import HotelNearbySearchRequest, HotelSearchResult
from app.locations.models import LocationCandidate, LocationQuery, ResolvedLocation
from app.locations.service import LocationServiceError


FETCHED_AT = datetime(2026, 8, 31, 8, 0, tzinfo=timezone.utc)


def resolved_location() -> ResolvedLocation:
    return ResolvedLocation(
        id="loc-1",
        name="厦门大学",
        latitude=24.438,
        longitude=118.097,
        address="测试地址",
        city="厦门市",
        district="思明区",
        province="福建省",
        provider="fake-location",
    )


def hotel_result() -> HotelSearchResult:
    return HotelSearchResult(
        items=[],
        total=0,
        page=1,
        page_size=10,
        provider="fake-hotel",
        status="success",
        fetched_at=FETCHED_AT,
    )


@dataclass
class FakeLocationService:
    resolved: ResolvedLocation | None = None
    error: Exception | None = None

    def __post_init__(self) -> None:
        self.queries: list[LocationQuery] = []

    def resolve(self, query: LocationQuery) -> ResolvedLocation:
        self.queries.append(query)
        if self.error is not None:
            raise self.error
        assert self.resolved is not None
        return self.resolved


@dataclass
class FakeHotelService:
    result: HotelSearchResult
    error: Exception | None = None

    def __post_init__(self) -> None:
        self.requests: list[HotelNearbySearchRequest] = []

    def search_nearby(self, request: HotelNearbySearchRequest) -> HotelSearchResult:
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        return self.result


def test_search_resolves_location_then_searches_nearby_hotels() -> None:
    from app.application.hotel_nearby import (
        HotelNearbyApplication,
        HotelNearbyApplicationRequest,
    )

    location = resolved_location()
    hotels = hotel_result()
    location_service = FakeLocationService(location)
    hotel_service = FakeHotelService(hotels)
    application = HotelNearbyApplication(
        location_service=location_service,
        hotel_service=hotel_service,
    )

    result = application.search(
        HotelNearbyApplicationRequest(
            location_query="厦门大学",
            city="厦门",
            radius=2000,
            keyword="酒店",
            page=1,
            page_size=10,
        )
    )

    assert location_service.queries == [
        LocationQuery(query="厦门大学", city="厦门")
    ]
    assert hotel_service.requests == [
        HotelNearbySearchRequest(
            latitude=24.438,
            longitude=118.097,
            radius=2000,
            keyword="酒店",
            page=1,
            page_size=10,
        )
    ]
    assert result.location is location
    assert result.hotels is hotels


def test_search_passes_latitude_and_longitude_without_swapping() -> None:
    from app.application.hotel_nearby import (
        HotelNearbyApplication,
        HotelNearbyApplicationRequest,
    )

    location = ResolvedLocation(
        id="loc-2",
        name="测试地点",
        latitude=24.123,
        longitude=118.987,
        provider="fake-location",
    )
    hotel_service = FakeHotelService(hotel_result())
    application = HotelNearbyApplication(
        location_service=FakeLocationService(location),
        hotel_service=hotel_service,
    )

    application.search(
        HotelNearbyApplicationRequest(location_query="测试地点")
    )

    request = hotel_service.requests[0]
    assert request.latitude == 24.123
    assert request.longitude == 118.987


def test_location_not_found_is_propagated_without_calling_hotel_service() -> None:
    from app.application.hotel_nearby import (
        HotelNearbyApplication,
        HotelNearbyApplicationRequest,
    )

    location_error = LocationServiceError("LOCATION_NOT_FOUND")
    hotel_service = FakeHotelService(hotel_result())
    application = HotelNearbyApplication(
        location_service=FakeLocationService(error=location_error),
        hotel_service=hotel_service,
    )

    with pytest.raises(LocationServiceError) as error:
        application.search(HotelNearbyApplicationRequest(location_query="不存在"))

    assert error.value is location_error
    assert error.value.code == "LOCATION_NOT_FOUND"
    assert hotel_service.requests == []


def test_location_ambiguous_is_propagated_without_calling_hotel_service() -> None:
    from app.application.hotel_nearby import (
        HotelNearbyApplication,
        HotelNearbyApplicationRequest,
    )

    candidates = [
        LocationCandidate(
            id="loc-1",
            name="万达广场",
            latitude=24.4,
            longitude=118.1,
            provider="fake-location",
        ),
        LocationCandidate(
            id="loc-2",
            name="万达广场",
            latitude=24.5,
            longitude=118.2,
            provider="fake-location",
        ),
    ]
    location_error = LocationServiceError(
        "LOCATION_AMBIGUOUS",
        candidates=candidates,
    )
    hotel_service = FakeHotelService(hotel_result())
    application = HotelNearbyApplication(
        location_service=FakeLocationService(error=location_error),
        hotel_service=hotel_service,
    )

    with pytest.raises(LocationServiceError) as error:
        application.search(HotelNearbyApplicationRequest(location_query="万达广场"))

    assert error.value is location_error
    assert error.value.code == "LOCATION_AMBIGUOUS"
    assert error.value.candidates == candidates
    assert hotel_service.requests == []


def test_hotel_provider_error_is_propagated_without_wrapping() -> None:
    from app.application.hotel_nearby import (
        HotelNearbyApplication,
        HotelNearbyApplicationRequest,
    )

    hotel_error = AppError("BAIDU_HOTEL_TIMEOUT", "hotel provider timed out")
    hotel_service = FakeHotelService(hotel_result(), error=hotel_error)
    application = HotelNearbyApplication(
        location_service=FakeLocationService(resolved_location()),
        hotel_service=hotel_service,
    )

    with pytest.raises(AppError) as error:
        application.search(HotelNearbyApplicationRequest(location_query="厦门大学"))

    assert error.value is hotel_error
    assert error.value.code == "BAIDU_HOTEL_TIMEOUT"
    assert len(hotel_service.requests) == 1


def test_empty_hotel_result_is_returned_with_resolved_location() -> None:
    from app.application.hotel_nearby import (
        HotelNearbyApplication,
        HotelNearbyApplicationRequest,
    )

    location = resolved_location()
    hotels = hotel_result()
    application = HotelNearbyApplication(
        location_service=FakeLocationService(location),
        hotel_service=FakeHotelService(hotels),
    )

    result = application.search(
        HotelNearbyApplicationRequest(location_query="厦门大学")
    )

    assert result.location is location
    assert result.hotels is hotels
    assert result.hotels.items == []
