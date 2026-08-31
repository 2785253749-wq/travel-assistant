from __future__ import annotations

import ast
from datetime import datetime, timezone
from pathlib import Path

from app.hotels.models import (
    HotelDetail,
    HotelNearbySearchRequest,
    HotelSearchRequest,
    HotelSearchResult,
    HotelSummary,
)
from app.hotels.provider import HotelProvider


FETCHED_AT = datetime(2026, 8, 31, 8, 0, tzinfo=timezone.utc)


class FakeHotelProvider:
    def __init__(self) -> None:
        self.requests: list[HotelSearchRequest | HotelNearbySearchRequest] = []
        self.detail_ids: list[str] = []

    def search(
        self, request: HotelSearchRequest | HotelNearbySearchRequest
    ) -> HotelSearchResult:
        self.requests.append(request)
        return HotelSearchResult(
            items=[
                HotelSummary(
                    id="fake:hotel-1",
                    name="测试酒店",
                    address="测试路 1 号",
                    provider="fake",
                )
            ],
            total=1,
            page=request.page,
            page_size=request.page_size,
            provider="fake",
            status="success",
            fetched_at=FETCHED_AT,
        )

    def get_detail(self, hotel_id: str) -> HotelDetail | None:
        self.detail_ids.append(hotel_id)
        if hotel_id != "fake:hotel-1":
            return None
        return HotelDetail(
            id=hotel_id,
            name="测试酒店",
            address="测试路 1 号",
            provider="fake",
            tags=["亲子"],
        )


def _use_provider(
    provider: HotelProvider,
    request: HotelSearchRequest | HotelNearbySearchRequest,
) -> tuple[HotelSearchResult, HotelDetail | None]:
    result = provider.search(request)
    detail = provider.get_detail(result.items[0].id)
    return result, detail


def test_fake_provider_satisfies_search_and_detail_contract() -> None:
    provider = FakeHotelProvider()
    result, detail = _use_provider(
        provider,
        HotelSearchRequest(city="厦门", keyword="海边", page=2, page_size=5),
    )

    assert result.status == "success"
    assert result.items[0].id == "fake:hotel-1"
    assert result.page == 2
    assert detail is not None
    assert detail.tags == ["亲子"]
    assert provider.detail_ids == ["fake:hotel-1"]


def test_one_search_method_accepts_city_and_nearby_requests() -> None:
    provider = FakeHotelProvider()
    city_request = HotelSearchRequest(city="厦门")
    nearby_request = HotelNearbySearchRequest(
        latitude=24.4798,
        longitude=118.0894,
        radius=3000,
    )

    city_result = provider.search(city_request)
    nearby_result = provider.search(nearby_request)

    assert provider.requests == [city_request, nearby_request]
    assert city_result.page_size == 10
    assert nearby_result.page_size == 10


def test_detail_lookup_treats_hotel_id_as_an_opaque_string() -> None:
    provider = FakeHotelProvider()

    assert provider.get_detail("provider-specific:hotel-42") is None
    assert provider.detail_ids == ["provider-specific:hotel-42"]


def test_provider_module_imports_only_domain_contract_dependencies() -> None:
    source_path = Path(__import__("app.hotels.provider").hotels.provider.__file__)
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

    assert "typing" in imported_modules
    assert "app.hotels.models" in imported_modules
    assert not any(name.startswith("app.agent") for name in imported_modules)
    assert not any("baidu" in name.lower() for name in imported_modules)
