import pytest

from app.schemas import WeatherCard


class AvailableWeatherService:
    def city_card(self, city_id: str) -> WeatherCard:
        assert city_id == "xiamen"
        return WeatherCard(
            city="厦门市",
            status="available",
            summary="多云，31°C，湿度 75%，南风 ≤3级",
            report_time="2026-08-12T14:30:00+08:00",
        )


class BrokenWeatherService:
    def city_card(self, city_id: str) -> WeatherCard:
        del city_id
        raise RuntimeError("programming defect")


def test_weather_api_returns_only_the_public_weather_card(client, monkeypatch) -> None:
    monkeypatch.setattr(
        "app.api.weather.get_weather_service",
        lambda: AvailableWeatherService(),
    )

    response = client.get("/api/weather/cities/xiamen")

    assert response.status_code == 200
    assert response.json() == {
        "city": "厦门市",
        "status": "available",
        "summary": "多云，31°C，湿度 75%，南风 ≤3级",
        "report_time": "2026-08-12T14:30:00+08:00",
    }


def test_weather_api_does_not_disguise_a_programming_defect_as_unavailable(client, monkeypatch) -> None:
    monkeypatch.setattr(
        "app.api.weather.get_weather_service",
        lambda: BrokenWeatherService(),
    )

    with pytest.raises(RuntimeError, match="programming defect"):
        client.get("/api/weather/cities/xiamen")


def test_missing_server_key_returns_unavailable_without_http(client, monkeypatch) -> None:
    from app import composition

    monkeypatch.delenv("AMAP_WEB_SERVICE_KEY", raising=False)
    monkeypatch.setenv("AMAP_JS_KEY", "browser-key-must-not-be-used")
    composition.get_weather_service.cache_clear()

    try:
        response = client.get("/api/weather/cities/xiamen")
    finally:
        composition.get_weather_service.cache_clear()

    assert response.status_code == 200
    assert response.json()["status"] == "unavailable"
    assert "browser-key-must-not-be-used" not in response.text
