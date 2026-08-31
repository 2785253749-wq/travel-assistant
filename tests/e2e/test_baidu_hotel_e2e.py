from __future__ import annotations

from dataclasses import dataclass
import logging
import os
from collections.abc import Iterator
from urllib.parse import quote

from httpx import Response
import pytest
from fastapi.testclient import TestClient

from app.composition import get_hotel_service
from app.core.config import get_settings
from app.main import app


_LOGGER = logging.getLogger("tests.e2e.baidu_hotel")
_Xiamen_GCJ02 = (24.4798, 118.0894)
_BAIDU_HOST = "api.map.baidu.com"


@dataclass(frozen=True)
class LiveHotelFlow:
    city_search: Response
    nearby_search: Response
    detail: Response | None


def _require_live_e2e() -> None:
    if os.getenv("RUN_BAIDU_HOTEL_E2E") != "1":
        pytest.skip("真实百度 Hotel E2E 未启用：请设置 RUN_BAIDU_HOTEL_E2E=1")

    get_settings.cache_clear()
    settings = get_settings()
    if settings.baidu_map_ak is None or not settings.baidu_map_ak.get_secret_value().strip():
        pytest.skip("真实百度 Hotel E2E 未执行，因为 BAIDU_MAP_AK 未配置")


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
def live_client() -> Iterator[TestClient]:
    _require_live_e2e()
    monkeypatch = pytest.MonkeyPatch()
    _ensure_baidu_direct_connection(monkeypatch)
    app.dependency_overrides.clear()
    get_settings.cache_clear()
    get_hotel_service.cache_clear()
    try:
        with TestClient(app) as client:
            yield client
    finally:
        app.dependency_overrides.clear()
        get_hotel_service.cache_clear()
        get_settings.cache_clear()
        monkeypatch.undo()


@pytest.fixture(scope="module")
def serial_hotel_flow(live_client: TestClient) -> LiveHotelFlow:
    # Keep the three live calls in one synchronous fixture: city -> nearby -> detail.
    city_search = live_client.get(
        "/api/hotels/search",
        params={"city": "厦门", "keyword": "酒店", "page": 1, "page_size": 10},
    )

    latitude, longitude = _Xiamen_GCJ02
    nearby_search = live_client.get(
        "/api/hotels/nearby",
        params={
            "latitude": latitude,
            "longitude": longitude,
            "radius": 2000,
            "keyword": "酒店",
            "page": 1,
            "page_size": 10,
        },
    )

    detail: Response | None = None
    if city_search.status_code == 200:
        payload = city_search.json()
        items = payload.get("items")
        first_item = items[0] if isinstance(items, list) and items else None
        hotel_id = first_item.get("id") if isinstance(first_item, dict) else None
        if isinstance(hotel_id, str) and hotel_id.strip():
            detail = live_client.get(f"/api/hotels/{quote(hotel_id, safe='')}")

    return LiveHotelFlow(
        city_search=city_search,
        nearby_search=nearby_search,
        detail=detail,
    )


def _assert_search_contract(payload: dict[str, object]) -> list[object]:
    assert payload["status"] == "success"
    assert payload["provider"] == "baidu"
    assert payload["page"] == 1
    assert payload["page_size"] == 10
    items = payload["items"]
    assert isinstance(items, list)
    return items


def _log_observation(kind: str, payload: dict[str, object], items: list[object]) -> None:
    optional_fields = (
        "rating",
        "telephone",
        "business_hours",
        "description",
        "detail_url",
        "tags",
        "distance",
    )
    present_fields = sorted(
        field
        for field in optional_fields
        if any(isinstance(item, dict) and item.get(field) is not None for item in items)
    )
    _LOGGER.info(
        "baidu_hotel_e2e_observation kind=%s status=%s total=%s results=%d optional_fields=%s",
        kind,
        payload.get("status"),
        payload.get("total"),
        len(items),
        ",".join(present_fields) or "none",
    )


def test_city_search_uses_live_baidu_hotel_chain(serial_hotel_flow: LiveHotelFlow) -> None:
    response = serial_hotel_flow.city_search
    assert response.status_code == 200
    payload = response.json()
    items = _assert_search_contract(payload)
    _log_observation("city", payload, items)

    if items:
        first = items[0]
        assert isinstance(first, dict)
        assert isinstance(first.get("id"), str) and first["id"].strip()
        assert isinstance(first.get("name"), str) and first["name"].strip()
        assert first["provider"] == "baidu"
        if first.get("latitude") is not None:
            assert -90 <= first["latitude"] <= 90
        if first.get("longitude") is not None:
            assert -180 <= first["longitude"] <= 180


def test_nearby_search_uses_live_baidu_hotel_chain(serial_hotel_flow: LiveHotelFlow) -> None:
    response = serial_hotel_flow.nearby_search
    assert response.status_code == 200
    payload = response.json()
    items = _assert_search_contract(payload)
    _log_observation("nearby", payload, items)

    for item in items:
        assert isinstance(item, dict)
        if item.get("distance") is not None:
            assert item["distance"] >= 0


def test_detail_uses_uid_from_live_city_search(serial_hotel_flow: LiveHotelFlow) -> None:
    city_response = serial_hotel_flow.city_search
    if city_response.status_code != 200:
        pytest.skip("城市酒店搜索未返回 200，跳过依赖实时 UID 的详情查询")

    search_items = city_response.json().get("items", [])
    if not search_items:
        pytest.skip("城市酒店搜索没有返回酒店，跳过详情查询")

    first_item = search_items[0]
    detail = serial_hotel_flow.detail
    if detail is None:
        pytest.skip("城市酒店搜索没有返回合法 UID，跳过详情查询")

    assert detail.status_code == 200
    payload = detail.json()
    assert payload["id"] == first_item["id"]
    assert isinstance(payload["name"], str) and payload["name"].strip()
    assert payload["provider"] == "baidu"
    _log_observation("detail", payload, [payload])
