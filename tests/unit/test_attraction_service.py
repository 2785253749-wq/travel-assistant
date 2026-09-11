from datetime import UTC, datetime

import pytest

from app.attractions.models import (
    AttractionNearbySearchRequest,
    AttractionSearchRequest,
    AttractionSearchResult,
)


FETCHED_AT = datetime(2026, 9, 9, 12, 0, tzinfo=UTC)


def _service_type():
    from app.attractions.service import AttractionService

    return AttractionService


def attraction_result(*, warning: str | None = None) -> AttractionSearchResult:
    return AttractionSearchResult(
        items=[],
        total=0,
        page=2,
        page_size=5,
        provider="fake",
        status="success",
        warning=warning,
        fetched_at=FETCHED_AT,
    )


class RecordingProvider:
    def __init__(self, result: AttractionSearchResult) -> None:
        self.result = result
        self.requests: list[
            AttractionSearchRequest | AttractionNearbySearchRequest
        ] = []

    def search(
        self,
        request: AttractionSearchRequest | AttractionNearbySearchRequest,
    ) -> AttractionSearchResult:
        self.requests.append(request)
        return self.result


class RaisingProvider:
    def search(
        self,
        request: AttractionSearchRequest | AttractionNearbySearchRequest,
    ) -> AttractionSearchResult:
        del request
        raise RuntimeError("provider failed")


def test_search_city_forwards_the_same_request_and_preserves_result() -> None:
    AttractionService = _service_type()
    expected_result = attraction_result(warning="partial")
    provider = RecordingProvider(expected_result)
    service = AttractionService(provider=provider)
    request = AttractionSearchRequest(
        city="厦门",
        keyword="校园景点",
        page=2,
        page_size=5,
        sort_by="rating",
    )

    result = service.search_city(request)

    assert provider.requests == [request]
    assert provider.requests[0] is request
    assert result is expected_result
    assert result.warning == "partial"


def test_search_nearby_forwards_the_same_request_and_preserves_result() -> None:
    AttractionService = _service_type()
    expected_result = attraction_result()
    provider = RecordingProvider(expected_result)
    service = AttractionService(provider=provider)
    request = AttractionNearbySearchRequest(
        latitude=24.44,
        longitude=118.09,
        radius=3000,
        keyword="景点",
        page=2,
        page_size=7,
        sort_by="distance",
    )

    result = service.search_nearby(request)

    assert provider.requests == [request]
    assert provider.requests[0] is request
    assert result is expected_result


def test_search_city_propagates_provider_exception_unchanged() -> None:
    AttractionService = _service_type()
    service = AttractionService(provider=RaisingProvider())

    with pytest.raises(RuntimeError, match="provider failed"):
        service.search_city(AttractionSearchRequest(city="厦门"))
