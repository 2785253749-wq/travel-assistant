from __future__ import annotations

import pytest

from app.core.config import Settings


def test_build_location_service_wires_baidu_provider() -> None:
    from app.composition import build_location_service
    from app.locations.service import LocationService
    from app.providers.baidu_location import BaiduLocationProvider

    service = build_location_service(
        settings=Settings(baidu_map_ak="test-baidu-ak", _env_file=None)
    )

    try:
        assert isinstance(service, LocationService)
        assert isinstance(service._provider, BaiduLocationProvider)
        assert service._provider._api_key == "test-baidu-ak"
    finally:
        service._provider._client.close()


def test_get_location_service_is_cached_and_uses_current_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app import composition

    settings = Settings(baidu_map_ak="test-baidu-ak", _env_file=None)
    monkeypatch.setattr(composition, "get_settings", lambda: settings)
    composition.get_location_service.cache_clear()

    try:
        first = composition.get_location_service()
        second = composition.get_location_service()

        assert first is second
    finally:
        first._provider._client.close()
        composition.get_location_service.cache_clear()


def test_missing_baidu_ak_fails_when_location_composition_is_used(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app import composition
    from app.providers.baidu_location import BaiduLocationProviderError

    monkeypatch.delenv("BAIDU_MAP_AK", raising=False)

    with pytest.raises(BaiduLocationProviderError) as error:
        composition.build_location_service(settings=Settings(_env_file=None))

    assert error.value.code == "BAIDU_LOCATION_NOT_CONFIGURED"
