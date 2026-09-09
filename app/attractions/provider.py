from __future__ import annotations

from typing import Protocol

from app.attractions.models import (
    AttractionNearbySearchRequest,
    AttractionSearchRequest,
    AttractionSearchResult,
)


class AttractionProvider(Protocol):
    """Synchronous boundary for attraction search providers."""

    def search(
        self,
        request: AttractionSearchRequest | AttractionNearbySearchRequest,
    ) -> AttractionSearchResult: ...
