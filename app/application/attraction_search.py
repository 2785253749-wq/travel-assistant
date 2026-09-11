from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.attractions.models import (
    AttractionNearbySearchRequest,
    AttractionSearchMode,
    AttractionSearchRequest,
    AttractionSearchResult,
    AttractionSortBy,
)
from app.attractions.service import AttractionService
from app.locations.models import LocationQuery
from app.locations.service import LocationService


@dataclass(frozen=True)
class AttractionCityApplicationRequest:
    city: str
    sort_by: Literal["rating"] | None = None
    page: int = 1
    page_size: int = 10


@dataclass(frozen=True)
class AttractionNearbyApplicationRequest:
    location_query: str
    city: str
    radius: int = 2000
    sort_by: AttractionSortBy | None = None
    page: int = 1
    page_size: int = 10


@dataclass(frozen=True)
class AttractionSearchApplicationResult:
    attractions: AttractionSearchResult
    mode: AttractionSearchMode
    city: str
    location_query: str | None
    radius: int | None


class AttractionSearchApplication:
    def __init__(
        self,
        *,
        location_service: LocationService,
        attraction_service: AttractionService,
    ) -> None:
        self._location_service = location_service
        self._attraction_service = attraction_service

    def search_city(
        self,
        request: AttractionCityApplicationRequest,
    ) -> AttractionSearchApplicationResult:
        if request.sort_by == "distance":
            raise ValueError("city search does not support distance sorting")

        attractions = self._attraction_service.search_city(
            AttractionSearchRequest(
                city=request.city,
                page=request.page,
                page_size=request.page_size,
                sort_by=request.sort_by,
            )
        )
        return AttractionSearchApplicationResult(
            attractions=attractions,
            mode="city",
            city=request.city,
            location_query=None,
            radius=None,
        )

    def search_nearby(
        self,
        request: AttractionNearbyApplicationRequest,
    ) -> AttractionSearchApplicationResult:
        location = self._location_service.resolve(
            LocationQuery(query=request.location_query, city=request.city)
        )
        attractions = self._attraction_service.search_nearby(
            AttractionNearbySearchRequest(
                latitude=location.latitude,
                longitude=location.longitude,
                radius=request.radius,
                page=request.page,
                page_size=request.page_size,
                sort_by=request.sort_by,
            )
        )
        return AttractionSearchApplicationResult(
            attractions=attractions,
            mode="nearby",
            city=request.city,
            location_query=request.location_query,
            radius=request.radius,
        )
