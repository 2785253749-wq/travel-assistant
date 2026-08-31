from __future__ import annotations

from typing import Protocol

from app.locations.models import LocationQuery, LocationSearchResult


class LocationProvider(Protocol):
    """Synchronous boundary for provider-independent location search."""

    def search(self, query: LocationQuery) -> LocationSearchResult: ...
