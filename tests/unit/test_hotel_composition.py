from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import SecretStr, ValidationError

from app.core.config import Settings


def test_settings_reads_baidu_ak_as_secret() -> None:
    settings = Settings(baidu_map_ak="test-baidu-ak", _env_file=None)

    assert isinstance(settings.baidu_map_ak, SecretStr)
    assert settings.baidu_map_ak.get_secret_value() == "test-baidu-ak"
    assert str(settings.baidu_map_ak) == "**********"
    assert "test-baidu-ak" not in repr(settings)


def test_settings_without_baidu_ak_still_loads() -> None:
    settings = Settings(_env_file=None)

    assert settings.baidu_map_ak is None


def test_hotel_timeout_has_default_and_environment_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("HOTEL_TIMEOUT_SECONDS", raising=False)
    default = Settings(_env_file=None)
    monkeypatch.setenv("HOTEL_TIMEOUT_SECONDS", "4.25")

    overridden = Settings(_env_file=None)

    assert default.hotel_timeout_seconds == 10.0
    assert overridden.hotel_timeout_seconds == 4.25


@pytest.mark.parametrize("value", ["0", "-1", "not-a-number"])
def test_hotel_timeout_rejects_invalid_values(
    value: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HOTEL_TIMEOUT_SECONDS", value)

    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_build_hotel_service_wires_baidu_provider_and_timeout() -> None:
    from app import composition
    from app.hotels.service import HotelService
    from app.providers.baidu_hotel import BaiduHotelProvider

    service = composition.build_hotel_service(
        settings=Settings(
            baidu_map_ak="test-baidu-ak",
            hotel_timeout_seconds=3.5,
            _env_file=None,
        )
    )

    try:
        assert isinstance(service, HotelService)
        assert isinstance(service._provider, BaiduHotelProvider)
        assert service._provider._timeout == 3.5
    finally:
        service._provider._client.close()


def test_get_hotel_service_is_cached_and_uses_current_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app import composition

    settings = Settings(
        baidu_map_ak="test-baidu-ak",
        hotel_timeout_seconds=4.0,
        _env_file=None,
    )
    monkeypatch.setattr(composition, "get_settings", lambda: settings)
    composition.get_hotel_service.cache_clear()

    try:
        first = composition.get_hotel_service()
        second = composition.get_hotel_service()

        assert first is second
    finally:
        first._provider._client.close()
        composition.get_hotel_service.cache_clear()


def test_missing_baidu_ak_fails_when_hotel_composition_is_used() -> None:
    from app import composition
    from app.providers.baidu_hotel import BaiduHotelProviderError

    with pytest.raises(BaiduHotelProviderError) as error:
        composition.build_hotel_service(settings=Settings(_env_file=None))

    assert error.value.code == "BAIDU_HOTEL_NOT_CONFIGURED"


def test_env_example_contains_only_empty_baidu_placeholder() -> None:
    env_example = Path(".env.example").read_text(encoding="utf-8")

    assert "BAIDU_MAP_AK=" in env_example
    assert "HOTEL_TIMEOUT_SECONDS=10" in env_example
    assert "test-baidu-ak" not in env_example
