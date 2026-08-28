from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from threading import Event, Lock
from time import monotonic
from typing import Callable, Protocol

from app.footprints.models import CityRecord, DistrictBoundary, DistrictBoundaryView
from app.footprints.service import StaticCityDirectory
from app.providers.base import ProviderResult


_TRIAL_CITY_ADCODES = ("350200", "350100", "532900", "530700")
_MAX_CACHE_ENTRIES = 256


class DistrictBoundaryProvider(Protocol):
    def search(self, query: str) -> list[CityRecord]: ...

    def boundary(self, adcode: str) -> ProviderResult[DistrictBoundary]: ...


@dataclass(slots=True)
class _CacheEntry:
    boundary: DistrictBoundary | None
    fallback_city: CityRecord | None
    fresh_until: float
    failure_until: float


class DistrictBoundaryService:
    """Canonical city lookup with bounded boundary caching and safe degradation."""

    def __init__(
        self,
        provider: DistrictBoundaryProvider,
        *,
        static_directory: StaticCityDirectory | None = None,
        cache_ttl_seconds: float = 2_592_000,
        failure_cache_seconds: float = 300,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        self._provider = provider
        self._static_directory = static_directory or StaticCityDirectory()
        self._cache_ttl_seconds = cache_ttl_seconds
        self._failure_cache_seconds = failure_cache_seconds
        self._clock = clock
        self._cache: OrderedDict[str, _CacheEntry] = OrderedDict()
        self._inflight: dict[str, Event] = {}
        self._lock = Lock()

    def search(self, query: str) -> list[CityRecord]:
        normalized_query = query.strip()
        if not normalized_query:
            return []
        candidates = self._static_matches(normalized_query)
        try:
            candidates.extend(self._provider.search(normalized_query))
        except Exception:
            pass
        return _unique_cities(candidates)[:10]

    def resolve(self, city_adcode: str) -> CityRecord | None:
        static_city = self._static_directory.resolve(city_adcode)
        if static_city is not None:
            return static_city
        try:
            candidates = self._provider.search(city_adcode)
        except Exception:
            return None
        return next(
            (city for city in candidates if city.city_adcode == city_adcode), None
        )

    def get_boundary(self, city_adcode: str) -> DistrictBoundaryView | None:
        while True:
            now = self._clock()
            with self._lock:
                entry = self._cache.get(city_adcode)
                if entry is not None:
                    self._cache.move_to_end(city_adcode)
                    cached = self._cached_view(entry, now)
                    if cached is not None:
                        return cached
                event = self._inflight.get(city_adcode)
                if event is None:
                    event = Event()
                    self._inflight[city_adcode] = event
                    break
            event.wait()

        entry = self._fetch_entry(city_adcode, previous=entry)
        with self._lock:
            self._cache[city_adcode] = entry
            self._cache.move_to_end(city_adcode)
            while len(self._cache) > _MAX_CACHE_ENTRIES:
                self._cache.popitem(last=False)
            self._inflight.pop(city_adcode).set()
            return self._cached_view(entry, self._clock())

    def _fetch_entry(
        self, city_adcode: str, *, previous: _CacheEntry | None
    ) -> _CacheEntry:
        now = self._clock()
        try:
            result = self._provider.boundary(city_adcode)
        except Exception:
            result = None
        boundary = (
            result.data
            if result is not None
            and result.data is not None
            and not result.degraded
            and result.data.city.city_adcode == city_adcode
            else None
        )
        if boundary is not None:
            return _CacheEntry(
                boundary=boundary,
                fallback_city=boundary.city,
                fresh_until=now + self._cache_ttl_seconds,
                failure_until=0,
            )
        return _CacheEntry(
            boundary=previous.boundary if previous is not None else None,
            fallback_city=(
                previous.fallback_city
                if previous is not None and previous.fallback_city is not None
                else self.resolve(city_adcode)
            ),
            fresh_until=previous.fresh_until if previous is not None else 0,
            failure_until=now + self._failure_cache_seconds,
        )

    def _cached_view(
        self, entry: _CacheEntry, now: float
    ) -> DistrictBoundaryView | None:
        if entry.boundary is not None:
            if now < entry.fresh_until:
                return _to_view(entry.boundary, "fresh")
            if now < entry.failure_until:
                return _to_view(entry.boundary, "stale")
        if now < entry.failure_until and entry.fallback_city is not None:
            return DistrictBoundaryView(
                city=entry.fallback_city,
                rings=[],
                status="unavailable",
            )
        return None

    def _static_matches(self, query: str) -> list[CityRecord]:
        return [
            city
            for adcode in _TRIAL_CITY_ADCODES
            if (city := self._static_directory.resolve(adcode)) is not None
            and _matches_city(city, query)
        ]


class UnavailableDistrictBoundaryService:
    """Credential-free static directory that never attempts an upstream request."""

    def __init__(self, *, static_directory: StaticCityDirectory | None = None) -> None:
        self._static_directory = static_directory or StaticCityDirectory()

    def search(self, query: str) -> list[CityRecord]:
        normalized_query = query.strip()
        if not normalized_query:
            return []
        return [
            city
            for adcode in _TRIAL_CITY_ADCODES
            if (city := self._static_directory.resolve(adcode)) is not None
            and _matches_city(city, normalized_query)
        ][:10]

    def resolve(self, city_adcode: str) -> CityRecord | None:
        return self._static_directory.resolve(city_adcode)

    def get_boundary(self, city_adcode: str) -> DistrictBoundaryView | None:
        city = self.resolve(city_adcode)
        if city is None:
            return None
        return DistrictBoundaryView(city=city, rings=[], status="unavailable")


def _to_view(
    boundary: DistrictBoundary, status: str
) -> DistrictBoundaryView:
    return DistrictBoundaryView(
        city=boundary.city,
        rings=boundary.rings,
        status=status,
    )


def _unique_cities(candidates: list[CityRecord]) -> list[CityRecord]:
    unique: dict[str, CityRecord] = {}
    for city in candidates:
        unique.setdefault(city.city_adcode, city)
    return list(unique.values())


def _matches_city(city: CityRecord, query: str) -> bool:
    return query in city.city_adcode or query in city.city_name or query in city.province_name


__all__ = [
    "DistrictBoundaryProvider",
    "DistrictBoundaryService",
    "UnavailableDistrictBoundaryService",
]
