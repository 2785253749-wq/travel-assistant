from __future__ import annotations

from collections.abc import Iterator
import os

import pytest

from app.composition import get_location_service
from app.core.config import get_settings
from app.locations.models import LocationQuery, LocationSearchResult


_BAIDU_HOST = "api.map.baidu.com"
_LOCATION_QUERIES = (
    ("厦门大学", LocationQuery(query="厦门大学", city="厦门")),
    ("鼓浪屿", LocationQuery(query="鼓浪屿", city="厦门")),
    ("泉州站", LocationQuery(query="泉州站", city="泉州")),
)


def _require_live_e2e() -> None:
    if os.getenv("RUN_BAIDU_LOCATION_E2E") != "1":
        pytest.skip("真实百度 Location E2E 未启用：请设置 RUN_BAIDU_LOCATION_E2E=1")

    get_settings.cache_clear()
    settings = get_settings()
    if settings.baidu_map_ak is None or not settings.baidu_map_ak.get_secret_value().strip():
        pytest.skip("真实百度 Location E2E 未执行，因为 BAIDU_MAP_AK 未配置")


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
def serial_location_observations() -> Iterator[dict[str, LocationSearchResult]]:
    _require_live_e2e()
    monkeypatch = pytest.MonkeyPatch()
    _ensure_baidu_direct_connection(monkeypatch)
    get_settings.cache_clear()
    get_location_service.cache_clear()
    service = get_location_service()
    try:
        observations: dict[str, LocationSearchResult] = {}
        for name, query in _LOCATION_QUERIES:
            observations[name] = service.search(query)
        yield observations
    finally:
        service._provider._client.close()
        get_location_service.cache_clear()
        get_settings.cache_clear()
        monkeypatch.undo()


def test_xiamen_university_returns_live_location_candidate(
    serial_location_observations: dict[str, LocationSearchResult],
) -> None:
    result = serial_location_observations["厦门大学"]

    assert result.provider == "baidu"
    assert isinstance(result.items, list)
    assert result.items
    assert any(
        item.name.strip()
        and -90 <= item.latitude <= 90
        and -180 <= item.longitude <= 180
        and item.provider == "baidu"
        for item in result.items
    )


def test_live_location_candidate_observations(
    serial_location_observations: dict[str, LocationSearchResult],
) -> None:
    for name, query in _LOCATION_QUERIES:
        result = serial_location_observations[name]
        print(
            f"location_observation query={name} "
            f"city={query.city} "
            f"candidate_count={len(result.items)}"
        )
        for candidate in result.items[:5]:
            summary = {
                "name": candidate.name,
                "city": candidate.city,
                "district": candidate.district,
                "address": candidate.address,
                "has_id": bool(candidate.id),
                "latitude": candidate.latitude,
                "longitude": candidate.longitude,
            }
            print(f"  candidate={summary}")
