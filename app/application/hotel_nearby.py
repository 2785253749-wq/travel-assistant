from __future__ import annotations

from dataclasses import dataclass

from app.hotels.models import HotelNearbySearchRequest, HotelSearchResult
from app.hotels.service import HotelService
from app.locations.models import LocationQuery, ResolvedLocation
from app.locations.service import LocationService


@dataclass(frozen=True)
class HotelNearbyApplicationRequest:
    location_query: str
    city: str | None = None
    radius: int = 2000
    keyword: str = "酒店"
    page: int = 1
    page_size: int = 10


@dataclass(frozen=True)
class HotelNearbyApplicationResult:
    location: ResolvedLocation
    hotels: HotelSearchResult


class HotelNearbyApplication:
    def __init__(
        self,
        *,
        location_service: LocationService,
        hotel_service: HotelService,
    ) -> None:
        self._location_service = location_service
        self._hotel_service = hotel_service

    def search(
        self,
        request: HotelNearbyApplicationRequest,
    ) -> HotelNearbyApplicationResult:
        location = self._location_service.resolve(
            LocationQuery(query=request.location_query, city=request.city)
        )
        hotels = self._hotel_service.search_nearby(
            HotelNearbySearchRequest(
                latitude=location.latitude,
                longitude=location.longitude,
                radius=request.radius,
                keyword=request.keyword,
                page=request.page,
                page_size=request.page_size,
            )
        )
        return HotelNearbyApplicationResult(location=location, hotels=hotels)
