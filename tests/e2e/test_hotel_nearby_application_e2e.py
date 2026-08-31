from __future__ import annotations

from collections.abc import Iterator
import os

import pytest

from app.application.hotel_nearby import (
    HotelNearbyApplicationRequest,
    HotelNearbyApplicationResult,
)
from app.composition import get_hotel_nearby_application
from app.core.config import get_settings
from app.locations.models import ResolvedLocation


_BAIDU_HOST = "api.map.baidu.com"


def _require_live_e2e() -> None:
    if os.getenv("RUN_HOTEL_NEARBY_E2E") != "1":
        pytest.skip("真实 Hotel Nearby E2E 未启用：请设置 RUN_HOTEL_NEARBY_E2E=1")

    get_settings.cache_clear()
    settings = get_settings()
    if settings.baidu_map_ak is None or not settings.baidu_map_ak.get_secret_value().strip():
        pytest.skip("真实 Hotel Nearby E2E 未执行，因为 BAIDU_MAP_AK 未配置")


def _ensure_baidu_direct_connection(monkeypatch: pytest.MonkeyPatch) -> None:
    inherited_values = [
        os.getenv("NO_PROXY", ""),
        os.getenv("no_proxy", ""),
    ]
    entries = [
        entry.strip()
        for value in inherited_values
        for entry in value.split(",")
        if entry.strip()
    ]
    normalized_entries = {
        entry.lower().lstrip(".").split(":", 1)[0] for entry in entries
    }
    if _BAIDU_HOST not in normalized_entries:
        entries.append(_BAIDU_HOST)
    updated_no_proxy = ",".join(dict.fromkeys(entries))
    monkeypatch.setenv("NO_PROXY", updated_no_proxy)
    monkeypatch.setenv("no_proxy", updated_no_proxy)


@pytest.fixture(scope="module")
def live_hotel_nearby_result() -> Iterator[HotelNearbyApplicationResult]:
    _require_live_e2e()
    monkeypatch = pytest.MonkeyPatch()
    _ensure_baidu_direct_connection(monkeypatch)
    get_settings.cache_clear()
    get_hotel_nearby_application.cache_clear()
    application = get_hotel_nearby_application()
    request = HotelNearbyApplicationRequest(
        location_query="厦门大学",
        city="厦门",
        radius=2000,
        keyword="酒店",
        page=1,
        page_size=10,
    )

    try:
        yield application.search(request)
    finally:
        application._location_service._provider._client.close()
        application._hotel_service._provider._client.close()
        get_hotel_nearby_application.cache_clear()
        get_settings.cache_clear()
        monkeypatch.undo()


def test_hotel_nearby_application_uses_live_location_and_hotel_chain(
    live_hotel_nearby_result: HotelNearbyApplicationResult,
) -> None:
    result = live_hotel_nearby_result

    assert isinstance(result.location, ResolvedLocation)
    assert result.location.name.strip()
    assert -90 <= result.location.latitude <= 90
    assert -180 <= result.location.longitude <= 180
    assert result.location.provider == "baidu"
    assert result.hotels.provider == "baidu"
    assert result.hotels.status == "success"
    assert isinstance(result.hotels.items, list)

    print(
        "resolved_location "
        f"name={result.location.name} "
        f"city={result.location.city} "
        f"district={result.location.district} "
        f"latitude={result.location.latitude} "
        f"longitude={result.location.longitude}"
    )
    print(
        "hotels "
        f"provider={result.hotels.provider} "
        f"status={result.hotels.status} "
        f"item_count={len(result.hotels.items)}"
    )
    for hotel in result.hotels.items[:3]:
        print(
            "  hotel="
            f"name={hotel.name} "
            f"address={hotel.address} "
            f"rating={hotel.rating} "
            f"distance={hotel.distance}"
        )
