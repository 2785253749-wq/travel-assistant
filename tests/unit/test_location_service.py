from __future__ import annotations

import pytest

from app.core.errors import AppError
from app.locations.models import (
    LocationCandidate,
    LocationQuery,
    LocationSearchResult,
    ResolvedLocation,
)
from app.locations.service import LocationService


def candidate(name: str, *, location_id: str) -> LocationCandidate:
    return LocationCandidate(
        id=location_id,
        name=name,
        latitude=24.4389,
        longitude=118.1019,
        address="厦门市思明区思明南路 422 号",
        city="厦门",
        district="思明区",
        province="福建省",
        provider="fake",
    )


class FakeLocationProvider:
    def __init__(
        self,
        result: LocationSearchResult | None = None,
        error: AppError | None = None,
    ) -> None:
        self.result = result
        self.error = error
        self.queries: list[LocationQuery] = []

    def search(self, query: LocationQuery) -> LocationSearchResult:
        self.queries.append(query)
        if self.error is not None:
            raise self.error
        assert self.result is not None
        return self.result


def test_resolve_raises_not_found_for_empty_provider_result() -> None:
    provider = FakeLocationProvider(
        LocationSearchResult(items=[], provider="fake")
    )
    service = LocationService(provider=provider)

    with pytest.raises(AppError) as error:
        service.resolve(LocationQuery(query="不存在的地点", city="厦门"))

    assert error.value.code == "LOCATION_NOT_FOUND"


def test_resolve_returns_single_candidate_as_resolved_location() -> None:
    expected = candidate("厦门大学", location_id="fake:location-1")
    provider = FakeLocationProvider(
        LocationSearchResult(items=[expected], provider="fake")
    )
    service = LocationService(provider=provider)

    resolved = service.resolve(LocationQuery(query="厦门大学", city="厦门"))

    assert isinstance(resolved, ResolvedLocation)
    assert resolved.id == expected.id
    assert resolved.name == expected.name
    assert resolved.latitude == expected.latitude
    assert resolved.longitude == expected.longitude
    assert resolved.address == expected.address
    assert resolved.city == expected.city
    assert resolved.district == expected.district
    assert resolved.province == expected.province
    assert resolved.provider == expected.provider


def test_resolve_raises_ambiguous_with_candidates_in_provider_order() -> None:
    candidates = [
        candidate("厦门大学思明校区", location_id="fake:location-1"),
        candidate("厦门大学翔安校区", location_id="fake:location-2"),
    ]
    provider = FakeLocationProvider(
        LocationSearchResult(items=candidates, provider="fake")
    )
    service = LocationService(provider=provider)

    with pytest.raises(AppError) as error:
        service.resolve(LocationQuery(query="厦门大学", city="厦门"))

    assert error.value.code == "LOCATION_AMBIGUOUS"
    assert error.value.candidates == candidates


def test_resolve_selects_the_unique_exact_name_candidate() -> None:
    candidates = [
        candidate("厦门大学思明校区", location_id="fake:location-1"),
        candidate("厦门大学", location_id="fake:location-2"),
        candidate("厦门大学翔安校区", location_id="fake:location-3"),
    ]
    provider = FakeLocationProvider(
        LocationSearchResult(items=candidates, provider="fake")
    )
    service = LocationService(provider=provider)

    resolved = service.resolve(LocationQuery(query="厦门大学", city="厦门"))

    assert isinstance(resolved, ResolvedLocation)
    assert resolved.id == "fake:location-2"
    assert resolved.name == "厦门大学"


def test_resolve_does_not_use_substring_matching() -> None:
    candidates = [
        candidate("厦门大学思明校区", location_id="fake:location-1"),
        candidate("厦门大学翔安校区", location_id="fake:location-2"),
    ]
    provider = FakeLocationProvider(
        LocationSearchResult(items=candidates, provider="fake")
    )
    service = LocationService(provider=provider)

    with pytest.raises(AppError) as error:
        service.resolve(LocationQuery(query="厦门大学", city="厦门"))

    assert error.value.code == "LOCATION_AMBIGUOUS"
    assert error.value.candidates == candidates


def test_resolve_keeps_all_candidates_for_multiple_exact_matches() -> None:
    candidates = [
        candidate("万达广场", location_id="fake:location-1"),
        candidate("万达广场", location_id="fake:location-2"),
        candidate("万达广场停车场", location_id="fake:location-3"),
    ]
    provider = FakeLocationProvider(
        LocationSearchResult(items=candidates, provider="fake")
    )
    service = LocationService(provider=provider)

    with pytest.raises(AppError) as error:
        service.resolve(LocationQuery(query="万达广场", city="厦门"))

    assert error.value.code == "LOCATION_AMBIGUOUS"
    assert error.value.candidates == candidates


@pytest.mark.parametrize(
    ("query_name", "candidate_name"),
    [
        ("  Xiamen   University ", "xiamen university"),
        ("  厦门大学  ", "厦门大学"),
    ],
)
def test_resolve_uses_only_light_name_normalization(
    query_name: str,
    candidate_name: str,
) -> None:
    expected = candidate(candidate_name, location_id="fake:location-2")
    provider = FakeLocationProvider(
        LocationSearchResult(
            items=[
                candidate("Xiamen University Siming Campus", location_id="fake:location-1"),
                expected,
            ],
            provider="fake",
        )
    )
    service = LocationService(provider=provider)

    resolved = service.resolve(LocationQuery(query=query_name, city="厦门"))

    assert resolved.id == expected.id
    assert resolved.name == expected.name


def test_resolve_accepts_a_single_non_exact_candidate() -> None:
    expected = candidate("鼓浪屿风景名胜区", location_id="fake:location-4")
    provider = FakeLocationProvider(
        LocationSearchResult(items=[expected], provider="fake")
    )
    service = LocationService(provider=provider)

    resolved = service.resolve(LocationQuery(query="鼓浪屿", city="厦门"))

    assert isinstance(resolved, ResolvedLocation)
    assert resolved.id == expected.id
    assert resolved.name == expected.name


def test_search_returns_provider_result_and_passes_query_unchanged() -> None:
    result = LocationSearchResult(
        items=[candidate("鼓浪屿", location_id="fake:location-3")],
        provider="fake",
    )
    provider = FakeLocationProvider(result)
    service = LocationService(provider=provider)
    query = LocationQuery(query="鼓浪屿", city="厦门")

    actual = service.search(query)

    assert actual is result
    assert provider.queries == [query]


def test_provider_app_error_is_propagated_without_mapping_or_swallowing() -> None:
    provider_error = AppError(
        "LOCATION_PROVIDER_UNAVAILABLE",
        "provider unavailable",
    )
    provider = FakeLocationProvider(error=provider_error)
    service = LocationService(provider=provider)

    with pytest.raises(AppError) as error:
        service.search(LocationQuery(query="泉州站"))

    assert error.value is provider_error
    assert error.value.code == "LOCATION_PROVIDER_UNAVAILABLE"
