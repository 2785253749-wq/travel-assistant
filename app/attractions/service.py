from __future__ import annotations

from app.attractions.models import (
    AttractionNearbySearchRequest,
    AttractionSearchRequest,
    AttractionSearchResult,
)
from app.attractions.provider import AttractionProvider


class AttractionService:
    """Stable application-facing entry point for attraction search."""

    def __init__(self, *, provider: AttractionProvider) -> None:
        self._provider = provider

    def search_city(
        self,
        request: AttractionSearchRequest,
    ) -> AttractionSearchResult:
        return self._provider.search(request)

    def search_nearby(
        self,
        request: AttractionNearbySearchRequest,
    ) -> AttractionSearchResult:
        return self._provider.search(request)
