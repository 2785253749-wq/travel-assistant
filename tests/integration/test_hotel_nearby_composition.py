from __future__ import annotations

import pytest

from app.core.config import Settings


def test_build_hotel_nearby_application_wires_location_and_hotel_services() -> None:
    from app.application.hotel_nearby import HotelNearbyApplication
    from app.composition import build_hotel_nearby_application
    from app.hotels.service import HotelService
    from app.locations.service import LocationService
    from app.providers.baidu_hotel import BaiduHotelProvider
    from app.providers.baidu_location import BaiduLocationProvider

    application = build_hotel_nearby_application(
        settings=Settings(baidu_map_ak="test-baidu-ak", _env_file=None)
    )

    try:
        assert isinstance(application, HotelNearbyApplication)
        assert isinstance(application._location_service, LocationService)
        assert isinstance(application._hotel_service, HotelService)
        assert isinstance(
            application._location_service._provider, BaiduLocationProvider
        )
        assert isinstance(application._hotel_service._provider, BaiduHotelProvider)
        assert application._location_service._provider._api_key == "test-baidu-ak"
        assert application._hotel_service._provider._api_key == "test-baidu-ak"
    finally:
        application._location_service._provider._client.close()
        application._hotel_service._provider._client.close()


def test_get_hotel_nearby_application_is_cached(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app import composition

    settings = Settings(baidu_map_ak="test-baidu-ak", _env_file=None)
    monkeypatch.setattr(composition, "get_settings", lambda: settings)
    composition.get_hotel_nearby_application.cache_clear()

    try:
        first = composition.get_hotel_nearby_application()
        second = composition.get_hotel_nearby_application()

        assert first is second
    finally:
        first._location_service._provider._client.close()
        first._hotel_service._provider._client.close()
        composition.get_hotel_nearby_application.cache_clear()


def test_missing_baidu_ak_preserves_provider_configuration_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app import composition
    from app.providers.baidu_location import BaiduLocationProviderError

    monkeypatch.delenv("BAIDU_MAP_AK", raising=False)

    with pytest.raises(BaiduLocationProviderError) as error:
        composition.build_hotel_nearby_application(settings=Settings(_env_file=None))

    assert error.value.code == "BAIDU_LOCATION_NOT_CONFIGURED"
