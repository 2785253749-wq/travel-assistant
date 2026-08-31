from __future__ import annotations

import re

from app.core.errors import AppError
from app.locations.models import (
    LocationCandidate,
    LocationQuery,
    LocationSearchResult,
    ResolvedLocation,
)
from app.locations.provider import LocationProvider


class LocationServiceError(AppError):
    """Expected resolution outcome with optional structured candidates."""

    def __init__(
        self,
        code: str,
        *,
        candidates: list[LocationCandidate] | None = None,
    ) -> None:
        super().__init__(code, code)
        self.candidates = list(candidates or [])


class LocationService:
    """Stable application-facing entry point for location operations."""

    def __init__(self, *, provider: LocationProvider) -> None:
        self._provider = provider

    def search(self, query: LocationQuery) -> LocationSearchResult:
        return self._provider.search(query)

    def resolve(self, query: LocationQuery) -> ResolvedLocation:
        result = self.search(query)
        if not result.items:
            raise LocationServiceError("LOCATION_NOT_FOUND")
        if len(result.items) > 1:
            normalized_query = _normalize_location_name(query.query)
            exact_matches = [
                candidate
                for candidate in result.items
                if _normalize_location_name(candidate.name) == normalized_query
            ]
            if len(exact_matches) != 1:
                raise LocationServiceError(
                    "LOCATION_AMBIGUOUS",
                    candidates=result.items,
                )
            candidate = exact_matches[0]
        else:
            candidate = result.items[0]
        return ResolvedLocation.model_validate(candidate.model_dump())


def _normalize_location_name(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip()).casefold()
