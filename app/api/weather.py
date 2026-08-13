from fastapi import APIRouter

from app.composition import get_weather_service
from app.schemas import WeatherCard


router = APIRouter(prefix="/api/weather", tags=["weather"])
_CITY_LABELS = {
    "xiamen": "厦门",
    "fujian": "福建",
    "yunnan": "云南",
}


@router.get("/cities/{city_id}", response_model=WeatherCard)
def city_weather(city_id: str) -> WeatherCard:
    return get_weather_service().city_card(city_id)
