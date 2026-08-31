from __future__ import annotations

from collections import OrderedDict
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date
import logging
from threading import Lock
from time import monotonic
from typing import Any, Callable

from app.providers.base import ProviderResult, utc_now
from app.providers.booking_links import BookingLinkBuilder, BookingLinks
from app.providers.free_weather import WEATHER_SOURCE, WeatherProvider
from app.providers.places import PLACES_SOURCE, PlacesProvider
from app.core.logging import operational_context
from app.schemas import TravelProfile
from app.trips.transport import TripTransportContext


PROVIDER_CACHE_TTL_SECONDS = 300.0
PROVIDER_CACHE_MAX_ENTRIES = 256


@dataclass(frozen=True)
class ProviderBundle:
    """One immutable snapshot passed from provider adapters to the planner."""

    results: tuple[ProviderResult[Any], ...]
    booking_links: BookingLinks
    transport_context: TripTransportContext | None = None

    @property
    def warnings(self) -> tuple[str, ...]:
        provider_warnings = tuple(
            result.error_code
            for result in self.results
            if result.degraded and result.error_code is not None
        )
        transport_warnings = (
            tuple(self.transport_context.warnings)
            if self.transport_context is not None
            else ()
        )
        return provider_warnings + transport_warnings


class ProviderEvidenceAggregator:
    """Fetch independent free providers without letting one failure abort planning."""

    def __init__(
        self,
        weather: WeatherProvider | None = None,
        places: PlacesProvider | None = None,
        booking_links: BookingLinkBuilder | None = None,
        cache_ttl_seconds: float = PROVIDER_CACHE_TTL_SECONDS,
        cache_max_entries: int = PROVIDER_CACHE_MAX_ENTRIES,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        if cache_max_entries < 1:
            raise ValueError("cache_max_entries must be positive")
        self._weather = weather or WeatherProvider()
        self._places = places or PlacesProvider()
        self._booking_links = booking_links or BookingLinkBuilder()
        self._cache_ttl_seconds = cache_ttl_seconds
        self._cache_max_entries = cache_max_entries
        self._clock = clock
        self._cache: OrderedDict[str, tuple[float, ProviderBundle]] = OrderedDict()
        self._inflight: dict[str, Future[ProviderBundle]] = {}
        self._cache_lock = Lock()

    def fetch(self, profile: TravelProfile) -> ProviderBundle:
        if not profile.destination or not profile.start_date or not profile.end_date:
            raise ValueError("provider aggregation requires a complete profile")
        cache_key = profile.model_dump_json()
        now = self._clock()
        leader = False
        with self._cache_lock:
            cached = self._cache.get(cache_key)
            if cached is not None and now < cached[0]:
                self._cache.move_to_end(cache_key)
                return cached[1]
            if cached is not None:
                del self._cache[cache_key]
            inflight = self._inflight.get(cache_key)
            if inflight is None:
                inflight = Future()
                self._inflight[cache_key] = inflight
                leader = True
        if not leader:
            return inflight.result()

        try:
            bundle = self._fetch_uncached(profile)
        except BaseException as exc:
            inflight.set_exception(exc)
            with self._cache_lock:
                self._inflight.pop(cache_key, None)
            raise

        with self._cache_lock:
            cache_now = self._clock()
            for expired_key, (expires_at, _) in tuple(self._cache.items()):
                if cache_now >= expires_at:
                    del self._cache[expired_key]
            while len(self._cache) >= self._cache_max_entries:
                self._cache.popitem(last=False)
            self._cache[cache_key] = (
                cache_now + self._cache_ttl_seconds,
                bundle,
            )
            self._inflight.pop(cache_key, None)
        inflight.set_result(bundle)
        return bundle

    def _fetch_uncached(self, profile: TravelProfile) -> ProviderBundle:
        assert profile.destination and profile.start_date and profile.end_date
        start = date.fromisoformat(profile.start_date)
        end = date.fromisoformat(profile.end_date)
        query = profile.preferences[0] if profile.preferences else f"{profile.destination} attractions"
        with ThreadPoolExecutor(max_workers=2, thread_name_prefix="provider-aggregate") as executor:
            weather_future = executor.submit(
                self._weather.forecast,
                profile.destination,
                start,
                end,
            )
            places_future = executor.submit(
                self._places.search,
                profile.destination,
                query,
            )
        try:
            weather = weather_future.result()
        except Exception:
            weather = ProviderResult(
                None,
                WEATHER_SOURCE,
                utc_now(),
                degraded=True,
                error_code="WEATHER_UNAVAILABLE",
            )
        try:
            places = places_future.result()
        except Exception:
            places = ProviderResult(
                [],
                PLACES_SOURCE,
                utc_now(),
                degraded=True,
                error_code="PLACES_UNAVAILABLE",
            )
        for provider, result in (("weather", weather), ("places", places)):
            logging.getLogger("app.provider").info(
                "provider_result",
                extra=operational_context(provider=provider, error_code=result.error_code),
            )
        bundle = ProviderBundle(
            results=(weather, places),
            booking_links=self._booking_links.build(profile),
        )
        return bundle
