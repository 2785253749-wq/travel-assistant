from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

import pytest

from app.attractions.models import (
    AttractionNearbySearchRequest,
    AttractionSearchRequest,
    AttractionSearchResult,
)
from app.core.errors import AppError
from app.locations.models import LocationCandidate, LocationQuery, ResolvedLocation
from app.locations.service import LocationServiceError


FETCHED_AT = datetime(2026, 9, 9, 12, 0, tzinfo=UTC)


def application_types():
    from app.application.attraction_search import (
        AttractionCityApplicationRequest,
        AttractionNearbyApplicationRequest,
        AttractionSearchApplication,
    )

    return (
        AttractionCityApplicationRequest,
        AttractionNearbyApplicationRequest,
        AttractionSearchApplication,
    )


def attraction_result(*, total: int | None = None) -> AttractionSearchResult:
    return AttractionSearchResult(
        items=[],
        total=total,
        page=1,
        page_size=10,
        provider="fake-attraction",
        status="success",
        warning=None,
        fetched_at=FETCHED_AT,
    )


def resolved_location() -> ResolvedLocation:
    return ResolvedLocation(
        id="location-1",
        name="厦门大学",
        latitude=24.123,
        longitude=118.987,
        provider="fake-location",
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
class FakeAttractionService:
    result: AttractionSearchResult
    error: Exception | None = None

    def __post_init__(self) -> None:
        self.city_requests: list[AttractionSearchRequest] = []
        self.nearby_requests: list[AttractionNearbySearchRequest] = []

    def search_city(self, request: AttractionSearchRequest) -> AttractionSearchResult:
        self.city_requests.append(request)
        if self.error is not None:
            raise self.error
        return self.result

    def search_nearby(
        self, request: AttractionNearbySearchRequest
    ) -> AttractionSearchResult:
        self.nearby_requests.append(request)
        if self.error is not None:
            raise self.error
        return self.result


def test_search_city_does_not_call_location_service() -> None:
    CityRequest, _, Application = application_types()
    result = attraction_result(total=None)
    location_service = FakeLocationService(resolved=resolved_location())
    attraction_service = FakeAttractionService(result)
    application = Application(
        location_service=location_service,
        attraction_service=attraction_service,
    )

    application_result = application.search_city(CityRequest(city="厦门"))

    assert location_service.queries == []
    assert attraction_service.city_requests == [
        AttractionSearchRequest(city="厦门")
    ]
    assert application_result.attractions is result
    assert application_result.mode == "city"
    assert application_result.city == "厦门"
    assert application_result.location_query is None
    assert application_result.radius is None


def test_search_city_forwards_rating_page_and_page_size() -> None:
    CityRequest, _, Application = application_types()
    attraction_service = FakeAttractionService(attraction_result())
    application = Application(
        location_service=FakeLocationService(resolved=resolved_location()),
        attraction_service=attraction_service,
    )

    application.search_city(
        CityRequest(city="厦门", sort_by="rating", page=2, page_size=7)
    )

    assert attraction_service.city_requests == [
        AttractionSearchRequest(
            city="厦门",
            page=2,
            page_size=7,
            sort_by="rating",
        )
    ]


def test_invalid_city_distance_never_reaches_attraction_service() -> None:
    CityRequest, _, Application = application_types()
    attraction_service = FakeAttractionService(attraction_result())
    application = Application(
        location_service=FakeLocationService(resolved=resolved_location()),
        attraction_service=attraction_service,
    )

    with pytest.raises(ValueError):
        application.search_city(  # type: ignore[arg-type]
            CityRequest(city="厦门", sort_by="distance")
        )

    assert attraction_service.city_requests == []


def test_search_nearby_resolves_location_then_forwards_coordinates() -> None:
    _, NearbyRequest, Application = application_types()
    result = attraction_result(total=None)
    location_service = FakeLocationService(resolved=resolved_location())
    attraction_service = FakeAttractionService(result)
    application = Application(
        location_service=location_service,
        attraction_service=attraction_service,
    )

    application_result = application.search_nearby(
        NearbyRequest(
            location_query="厦门大学",
            city="厦门",
            radius=3000,
            page=2,
            page_size=7,
        )
    )

    assert location_service.queries == [
        LocationQuery(query="厦门大学", city="厦门")
    ]
    assert attraction_service.nearby_requests == [
        AttractionNearbySearchRequest(
            latitude=24.123,
            longitude=118.987,
            radius=3000,
            page=2,
            page_size=7,
        )
    ]
    assert application_result.attractions is result
    assert application_result.mode == "nearby"
    assert application_result.city == "厦门"
    assert application_result.location_query == "厦门大学"
    assert application_result.radius == 3000


def test_search_nearby_with_confirmed_location_skips_location_resolution() -> None:
    _, NearbyRequest, Application = application_types()
    confirmed = ResolvedLocation(
        id="chengdu-kuanzhai-1",
        name="宽窄巷子",
        latitude=30.6631,
        longitude=104.0550,
        address="四川省成都市青羊区长顺上街127号",
        city="成都",
        district="青羊区",
        province="四川省",
        provider="fake-location",
    )
    location_service = FakeLocationService(resolved=confirmed)
    attraction_service = FakeAttractionService(attraction_result())
    application = Application(
        location_service=location_service,
        attraction_service=attraction_service,
    )

    application.search_nearby(
        NearbyRequest(
            location_query="宽窄巷子",
            city="成都",
            radius=2000,
            sort_by="rating",
            resolved_location=confirmed,
        )
    )

    assert location_service.queries == []
    assert attraction_service.nearby_requests == [
        AttractionNearbySearchRequest(
            latitude=30.6631,
            longitude=104.0550,
            radius=2000,
            sort_by="rating",
        )
    ]


def test_search_nearby_does_not_swap_latitude_and_longitude() -> None:
    _, NearbyRequest, Application = application_types()
    attraction_service = FakeAttractionService(attraction_result())
    application = Application(
        location_service=FakeLocationService(resolved=resolved_location()),
        attraction_service=attraction_service,
    )

    application.search_nearby(NearbyRequest(location_query="厦门大学", city="厦门"))

    request = attraction_service.nearby_requests[0]
    assert request.latitude == 24.123
    assert request.longitude == 118.987


@pytest.mark.parametrize("sort_by", ["rating", "distance"])
def test_search_nearby_forwards_radius_and_sort(
    sort_by: str,
) -> None:
    _, NearbyRequest, Application = application_types()
    attraction_service = FakeAttractionService(attraction_result())
    application = Application(
        location_service=FakeLocationService(resolved=resolved_location()),
        attraction_service=attraction_service,
    )

    application.search_nearby(
        NearbyRequest(
            location_query="厦门大学",
            city="厦门",
            radius=5000,
            sort_by=sort_by,  # type: ignore[arg-type]
        )
    )

    request = attraction_service.nearby_requests[0]
    assert request.radius == 5000
    assert request.sort_by == sort_by


def test_location_not_found_is_propagated_without_attraction_call() -> None:
    _, NearbyRequest, Application = application_types()
    location_error = LocationServiceError("LOCATION_NOT_FOUND")
    attraction_service = FakeAttractionService(attraction_result())
    application = Application(
        location_service=FakeLocationService(error=location_error),
        attraction_service=attraction_service,
    )

    with pytest.raises(LocationServiceError) as error:
        application.search_nearby(
            NearbyRequest(location_query="不存在", city="厦门")
        )

    assert error.value is location_error
    assert attraction_service.nearby_requests == []


def test_location_ambiguous_is_propagated_without_attraction_call() -> None:
    _, NearbyRequest, Application = application_types()
    candidates = [
        LocationCandidate(
            id="location-1",
            name="厦门大学",
            latitude=24.123,
            longitude=118.987,
            provider="fake-location",
        ),
        LocationCandidate(
            id="location-2",
            name="厦门大学漳州校区",
            latitude=24.456,
            longitude=117.789,
            provider="fake-location",
        ),
    ]
    location_error = LocationServiceError(
        "LOCATION_AMBIGUOUS",
        candidates=candidates,
    )
    attraction_service = FakeAttractionService(attraction_result())
    application = Application(
        location_service=FakeLocationService(error=location_error),
        attraction_service=attraction_service,
    )

    with pytest.raises(LocationServiceError) as error:
        application.search_nearby(
            NearbyRequest(location_query="厦门大学", city="厦门")
        )

    assert error.value is location_error
    assert error.value.candidates == candidates
    assert attraction_service.nearby_requests == []


def test_attraction_service_error_is_propagated_unchanged() -> None:
    _, NearbyRequest, Application = application_types()
    attraction_error = AppError(
        "BAIDU_ATTRACTION_PROVIDER_ERROR", "provider failed"
    )
    attraction_service = FakeAttractionService(
        attraction_result(), error=attraction_error
    )
    application = Application(
        location_service=FakeLocationService(resolved=resolved_location()),
        attraction_service=attraction_service,
    )

    with pytest.raises(AppError) as error:
        application.search_nearby(
            NearbyRequest(location_query="厦门大学", city="厦门")
        )

    assert error.value is attraction_error
    assert len(attraction_service.nearby_requests) == 1


def test_empty_result_and_total_none_are_preserved() -> None:
    CityRequest, _, Application = application_types()
    result = attraction_result(total=None)
    attraction_service = FakeAttractionService(result)
    application = Application(
        location_service=FakeLocationService(resolved=resolved_location()),
        attraction_service=attraction_service,
    )

    application_result = application.search_city(CityRequest(city="厦门"))

    assert application_result.attractions is result
    assert application_result.attractions.items == []
    assert application_result.attractions.total is None
