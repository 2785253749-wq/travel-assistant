from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from threading import Event, Lock

import pytest

from app.footprints.models import CityRecord, DistrictBoundary
from app.providers.base import ProviderResult


CITY = CityRecord(
    city_adcode="350200",
    city_name="厦门市",
    province_adcode="350000",
    province_name="福建省",
    center=(118.09, 24.48),
)


class Clock:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


class FakeDistrictProvider:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.fail = False
        self.started = Event()
        self.release = Event()
        self.block = False
        self._lock = Lock()

    def search(self, query: str) -> list[CityRecord]:
        if query == "杭州":
            return [
                CityRecord(
                    city_adcode="330100",
                    city_name="杭州市",
                    province_adcode="330000",
                    province_name="浙江省",
                    center=(120.15, 30.28),
                )
            ]
        return []

    def boundary(self, adcode: str) -> ProviderResult[DistrictBoundary]:
        with self._lock:
            self.calls.append(adcode)
        self.started.set()
        if self.block:
            assert self.release.wait(timeout=1)
        if self.fail:
            return ProviderResult(
                data=None,
                source="test",
                fetched_at=datetime.now(UTC),
                degraded=True,
                error_code="UPSTREAM_UNAVAILABLE",
            )
        return ProviderResult(
            data=DistrictBoundary(
                city=CITY,
                rings=[[(118.0, 24.0), (119.0, 24.0), (119.0, 25.0), (118.0, 24.0)]],
                fetched_at=datetime.now(UTC),
            ),
            source="test",
            fetched_at=datetime.now(UTC),
        )


class RaisingStaticDirectory:
    def resolve(self, _city_adcode: str) -> CityRecord | None:
        raise RuntimeError("private directory detail")


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def provider() -> FakeDistrictProvider:
    return FakeDistrictProvider()


@pytest.fixture
def service(provider: FakeDistrictProvider, clock: Clock):
    from app.footprints.districts import DistrictBoundaryService

    return DistrictBoundaryService(provider, clock=clock)


def test_fresh_cache_avoids_second_provider_call(service, provider):
    service.get_boundary("350200")
    service.get_boundary("350200")

    assert provider.calls == ["350200"]


def test_concurrent_cache_miss_is_single_flight(service, provider):
    provider.block = True
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(service.get_boundary, "350200") for _ in range(4)]
        assert provider.started.wait(timeout=1)
        provider.release.set()
        results = [future.result(timeout=1) for future in futures]

    assert provider.calls == ["350200"]
    assert [result.status for result in results] == ["fresh"] * 4


def test_expired_success_becomes_stale_when_provider_fails(service, provider, clock):
    assert service.get_boundary("350200").status == "fresh"
    clock.advance(2_592_001)
    provider.fail = True

    result = service.get_boundary("350200")

    assert result.status == "stale"
    assert result.city == CITY
    assert provider.calls == ["350200", "350200"]


def test_failure_cache_suppresses_retries_for_300_seconds(service, provider, clock):
    provider.fail = True

    assert service.get_boundary("350200").status == "unavailable"
    assert service.get_boundary("350200").status == "unavailable"
    assert provider.calls == ["350200"]
    clock.advance(301)

    service.get_boundary("350200")

    assert provider.calls == ["350200", "350200"]


def test_negative_failure_cache_suppresses_retries_without_a_boundary_or_city(
    service, provider, clock
):
    provider.fail = True

    assert service.get_boundary("999999") is None
    assert service.get_boundary("999999") is None
    clock.advance(301)
    assert service.get_boundary("999999") is None

    assert provider.calls == ["999999", "999999"]


def test_concurrent_negative_cache_miss_is_single_flight_without_a_city(
    service, provider
):
    provider.fail = True
    provider.block = True
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(service.get_boundary, "999999") for _ in range(4)]
        assert provider.started.wait(timeout=1)
        provider.release.set()
        assert [future.result(timeout=1) for future in futures] == [None] * 4

    assert provider.calls == ["999999"]


def test_directory_failure_is_published_as_a_safe_negative_cache(provider, clock):
    from app.footprints.districts import DistrictBoundaryService

    provider.fail = True
    service = DistrictBoundaryService(
        provider,
        static_directory=RaisingStaticDirectory(),
        clock=clock,
    )

    assert service.get_boundary("999999") is None
    assert service.get_boundary("999999") is None

    assert provider.calls == ["999999"]


def test_static_trial_cities_are_resolved_and_searched_without_provider(service, provider):
    assert service.resolve("350200") == CITY
    assert service.search("厦门") == [CITY]

    assert provider.calls == []


def test_search_combines_static_and_provider_candidates_without_duplicates(service):
    cities = service.search("杭州")

    assert [city.city_adcode for city in cities] == ["330100"]


def test_unavailable_service_preserves_trial_city_center_without_upstream_access():
    from app.footprints.districts import UnavailableDistrictBoundaryService

    result = UnavailableDistrictBoundaryService().get_boundary("350200")

    assert result.status == "unavailable"
    assert result.city == CITY
    assert result.rings == []
