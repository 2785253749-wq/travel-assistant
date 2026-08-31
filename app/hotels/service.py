from __future__ import annotations

from app.hotels.models import (
    HotelDetail,
    HotelNearbySearchRequest,
    HotelSearchRequest,
    HotelSearchResult,
)
from app.hotels.provider import HotelProvider


class HotelService:
    """Stable application-facing entry point for hotel operations."""

    def __init__(self, *, provider: HotelProvider) -> None:
        self._provider = provider

    def search_city(self, request: HotelSearchRequest) -> HotelSearchResult:
        return self._provider.search(request)

    def search_nearby(
        self,
        request: HotelNearbySearchRequest,
    ) -> HotelSearchResult:
        return self._provider.search(request)

    def get_detail(self, hotel_id: str) -> HotelDetail | None:
        if not isinstance(hotel_id, str):
            raise ValueError("hotel_id must be a non-empty string")
        normalized_id = hotel_id.strip()
        if not normalized_id:
            raise ValueError("hotel_id must be a non-empty string")
        return self._provider.get_detail(normalized_id)
