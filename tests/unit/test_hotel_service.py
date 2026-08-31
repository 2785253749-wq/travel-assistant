from __future__ import annotations

import ast
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.hotels.models import (
    HotelDetail,
    HotelNearbySearchRequest,
    HotelSearchRequest,
    HotelSearchResult,
    HotelSummary,
)
from app.hotels.service import HotelService


FETCHED_AT = datetime(2026, 8, 31, 8, 0, tzinfo=timezone.utc)


def hotel_result(*names: str, warning: str | None = None) -> HotelSearchResult:
    return HotelSearchResult(
        items=[
            HotelSummary(id=f"hotel-{index}", name=name, provider="fake")
            for index, name in enumerate(names, start=1)
        ],
        total=len(names),
        page=2,
        page_size=5,
        provider="fake",
        status="success",
        warning=warning,
        fetched_at=FETCHED_AT,
    )


class FakeHotelProvider:
    def __init__(
        self,
        *,
        search_result: HotelSearchResult | None = None,
        detail_result: HotelDetail | None = None,
        search_error: Exception | None = None,
        detail_error: Exception | None = None,
    ) -> None:
        self.search_result = search_result or hotel_result("酒店 A", "酒店 B", "酒店 C")
        self.detail_result = detail_result
        self.search_error = search_error
        self.detail_error = detail_error
        self.search_requests: list[HotelSearchRequest | HotelNearbySearchRequest] = []
        self.detail_ids: list[str] = []

    def search(
        self, request: HotelSearchRequest | HotelNearbySearchRequest
    ) -> HotelSearchResult:
        self.search_requests.append(request)
        if self.search_error is not None:
            raise self.search_error
        return self.search_result

    def get_detail(self, hotel_id: str) -> HotelDetail | None:
        self.detail_ids.append(hotel_id)
        if self.detail_error is not None:
            raise self.detail_error
        return self.detail_result


def test_service_accepts_a_provider_dependency() -> None:
    provider = FakeHotelProvider()

    service = HotelService(provider=provider)

    assert service is not None


def test_search_city_forwards_the_same_request_once_and_preserves_result() -> None:
    provider = FakeHotelProvider(
        search_result=hotel_result("酒店 A", "酒店 B", "酒店 C", warning="partial")
    )
    service = HotelService(provider=provider)
    request = HotelSearchRequest(city="厦门", keyword="酒店", page=2, page_size=5)

    result = service.search_city(request)

    assert provider.search_requests == [request]
    assert result is provider.search_result
    assert [item.name for item in result.items] == ["酒店 A", "酒店 B", "酒店 C"]
    assert result.total == 3
    assert result.page == 2
    assert result.page_size == 5
    assert result.provider == "fake"
    assert result.status == "success"
    assert result.warning == "partial"
    assert result.fetched_at == FETCHED_AT


def test_search_nearby_forwards_the_same_request_once() -> None:
    provider = FakeHotelProvider(search_result=hotel_result("附近酒店"))
    service = HotelService(provider=provider)
    request = HotelNearbySearchRequest(
        latitude=24.4798,
        longitude=118.0894,
        radius=3000,
        keyword="海景酒店",
        page=2,
        page_size=7,
    )

    result = service.search_nearby(request)

    assert provider.search_requests == [request]
    assert result is provider.search_result
    received = provider.search_requests[0]
    assert isinstance(received, HotelNearbySearchRequest)
    assert received.latitude == 24.4798
    assert received.longitude == 118.0894
    assert received.radius == 3000
    assert received.keyword == "海景酒店"
    assert received.page == 2
    assert received.page_size == 7


def test_service_does_not_sort_filter_or_deduplicate_provider_results() -> None:
    provider = FakeHotelProvider(search_result=hotel_result("酒店 A", "酒店 B", "酒店 A"))
    service = HotelService(provider=provider)

    result = service.search_city(HotelSearchRequest(city="厦门"))

    assert [item.name for item in result.items] == ["酒店 A", "酒店 B", "酒店 A"]


def test_empty_provider_result_is_returned_normally() -> None:
    result = hotel_result(warning="no hotels")
    result = result.model_copy(update={"items": [], "total": 0})
    provider = FakeHotelProvider(search_result=result)

    returned = HotelService(provider=provider).search_city(
        HotelSearchRequest(city="厦门")
    )

    assert returned is result
    assert returned.items == []
    assert returned.status == "success"


def test_get_detail_strips_id_and_forwards_once() -> None:
    detail = HotelDetail(id="hotel-123", name="酒店 A", provider="fake")
    provider = FakeHotelProvider(detail_result=detail)

    returned = HotelService(provider=provider).get_detail("  hotel-123  ")

    assert provider.detail_ids == ["hotel-123"]
    assert returned is detail


def test_missing_detail_is_returned_as_none() -> None:
    provider = FakeHotelProvider(detail_result=None)

    returned = HotelService(provider=provider).get_detail("hotel-404")

    assert returned is None
    assert provider.detail_ids == ["hotel-404"]


@pytest.mark.parametrize("hotel_id", ["", "   "])
def test_blank_detail_id_is_rejected_before_provider_call(hotel_id: str) -> None:
    provider = FakeHotelProvider()

    with pytest.raises(ValueError, match="hotel_id"):
        HotelService(provider=provider).get_detail(hotel_id)

    assert provider.detail_ids == []


def test_search_provider_exception_is_not_swallowed() -> None:
    error = RuntimeError("provider search failed")
    provider = FakeHotelProvider(search_error=error)

    with pytest.raises(RuntimeError, match="provider search failed"):
        HotelService(provider=provider).search_city(HotelSearchRequest(city="厦门"))


def test_detail_provider_exception_is_not_swallowed() -> None:
    error = RuntimeError("provider detail failed")
    provider = FakeHotelProvider(detail_error=error)

    with pytest.raises(RuntimeError, match="provider detail failed"):
        HotelService(provider=provider).get_detail("hotel-123")


def test_service_module_imports_only_domain_contract_dependencies() -> None:
    import app.hotels.service as service_module

    source_path = Path(service_module.__file__)
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    imported_modules = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    }
    imported_modules.update(
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    )

    assert "app.hotels.models" in imported_modules
    assert "app.hotels.provider" in imported_modules
    assert not any(name.startswith("app.providers") for name in imported_modules)
    assert not any(name.startswith("app.agent") for name in imported_modules)
