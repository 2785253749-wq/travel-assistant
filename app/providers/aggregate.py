from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import logging
from typing import Any

from app.providers.base import ProviderResult, utc_now
from app.providers.booking_links import BookingLinkBuilder, BookingLinks
from app.providers.free_weather import WEATHER_SOURCE, WeatherProvider
from app.providers.places import PLACES_SOURCE, PlacesProvider
from app.core.logging import operational_context
from app.schemas import TravelProfile


@dataclass(frozen=True)
class ProviderBundle:
    """One immutable snapshot passed from provider adapters to the planner."""

    results: tuple[ProviderResult[Any], ...]
    booking_links: BookingLinks

    @property
    def warnings(self) -> tuple[str, ...]:
        return tuple(
            result.error_code
            for result in self.results
            if result.degraded and result.error_code is not None
        )


class ProviderEvidenceAggregator:
    """Fetch independent free providers without letting one failure abort planning."""

    def __init__(
        self,
        weather: WeatherProvider | None = None,
        places: PlacesProvider | None = None,
        booking_links: BookingLinkBuilder | None = None,
    ) -> None:
        self._weather = weather or WeatherProvider()
        self._places = places or PlacesProvider()
        self._booking_links = booking_links or BookingLinkBuilder()

    def fetch(self, profile: TravelProfile) -> ProviderBundle:
        if not profile.destination or not profile.start_date or not profile.end_date:
            raise ValueError("provider aggregation requires a complete profile")
        start = date.fromisoformat(profile.start_date)
        end = date.fromisoformat(profile.end_date)
        try:
            weather = self._weather.forecast(profile.destination, start, end)
        except Exception:
            weather = ProviderResult(
                None,
                WEATHER_SOURCE,
                utc_now(),
                degraded=True,
                error_code="WEATHER_UNAVAILABLE",
            )
        query = profile.preferences[0] if profile.preferences else f"{profile.destination} attractions"
        try:
            places = self._places.search(profile.destination, query)
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
        return ProviderBundle(
            results=(weather, places),
            booking_links=self._booking_links.build(profile),
        )
