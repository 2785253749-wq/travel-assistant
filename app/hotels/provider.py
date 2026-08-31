from __future__ import annotations

from typing import Protocol

from app.hotels.models import (
    HotelDetail,
    HotelNearbySearchRequest,
    HotelSearchRequest,
    HotelSearchResult,
)


class HotelProvider(Protocol):
    """Synchronous boundary for hotel search and detail providers."""

    def search(
        self, request: HotelSearchRequest | HotelNearbySearchRequest
    ) -> HotelSearchResult: ...

    def get_detail(self, hotel_id: str) -> HotelDetail | None: ...
