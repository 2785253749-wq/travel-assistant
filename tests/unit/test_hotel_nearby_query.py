import pytest

from app.agent.hotel_nearby_query import HotelNearbyQueryExtractor


def test_extracts_location_city_and_unspecified_radius() -> None:
    result = HotelNearbyQueryExtractor().extract("帮我找厦门大学附近的酒店")

    assert result.location_query == "厦门大学"
    assert result.city == "厦门"
    assert result.radius is None


def test_extracts_quanzhou_station_and_city() -> None:
    result = HotelNearbyQueryExtractor().extract("泉州站附近住哪里")

    assert result.location_query == "泉州站"
    assert result.city == "泉州"
    assert result.radius is None


def test_does_not_guess_city_for_gulangyu() -> None:
    result = HotelNearbyQueryExtractor().extract("鼓浪屿附近酒店")

    assert result.location_query == "鼓浪屿"
    assert result.city is None


def test_does_not_guess_city_for_wanda_plaza() -> None:
    result = HotelNearbyQueryExtractor().extract("万达广场附近酒店")

    assert result.location_query == "万达广场"
    assert result.city is None


def test_extracts_explicit_city_without_removing_poi_name() -> None:
    result = HotelNearbyQueryExtractor().extract("厦门的鼓浪屿附近酒店")

    assert result.location_query == "鼓浪屿"
    assert result.city == "厦门"


@pytest.mark.parametrize(
    ("message", "radius"),
    [
        ("厦门大学附近500米的酒店", 500),
        ("厦门大学附近 500 米的酒店", 500),
        ("厦门大学附近2公里的酒店", 2000),
        ("厦门大学附近0.5公里的酒店", 500),
        ("厦门大学附近3.5公里的酒店", 3500),
        ("厦门大学附近两公里的酒店", 2000),
    ],
)
def test_extracts_radius_in_meters(message: str, radius: int) -> None:
    result = HotelNearbyQueryExtractor().extract(message)

    assert result.radius == radius
    assert result.invalid_fields == ()


def test_unspecified_radius_remains_none() -> None:
    result = HotelNearbyQueryExtractor().extract("厦门大学附近酒店")

    assert result.radius is None
    assert result.invalid_fields == ()


@pytest.mark.parametrize(
    ("message", "radius"),
    [
        ("厦门大学附近100米酒店", 100),
        ("厦门大学附近30公里酒店", 30000),
        ("厦门大学附近0公里酒店", 0),
    ],
)
def test_marks_radius_outside_chat_range_invalid(message: str, radius: int) -> None:
    result = HotelNearbyQueryExtractor().extract(message)

    assert result.radius == radius
    assert result.invalid_fields == ("radius",)


def test_missing_location_is_structured_without_an_exception() -> None:
    result = HotelNearbyQueryExtractor().extract("找附近酒店")

    assert result.location_query is None
    assert result.missing_fields == ("location_query",)


@pytest.mark.parametrize(
    "message",
    [
        "帮我查厦门大学周边住宿",
        "看看厦门大学周围的酒店",
        "找一下泉州站附近的酒店",
    ],
)
def test_extracts_location_from_supported_query_prefixes_and_nearby_terms(message: str) -> None:
    result = HotelNearbyQueryExtractor().extract(message)

    assert result.location_query in {"厦门大学", "泉州站"}


@pytest.mark.parametrize("message", ["附近有什么酒店", "帮我找周边住宿"])
def test_supported_nearby_request_without_location_remains_missing(message: str) -> None:
    assert HotelNearbyQueryExtractor().extract(message).missing_fields == ("location_query",)
