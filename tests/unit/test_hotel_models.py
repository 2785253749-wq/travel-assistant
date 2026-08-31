from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.hotels.models import (
    HotelDetail,
    HotelNearbySearchRequest,
    HotelSearchRequest,
    HotelSearchResult,
    HotelSummary,
)


FETCHED_AT = datetime(2026, 8, 31, 8, 30, tzinfo=UTC)


def summary(**overrides) -> HotelSummary:
    values = {
        "id": "baidu-hotel-1",
        "name": "厦门海景酒店",
        "address": "厦门市思明区环岛路 1 号",
        "latitude": 24.4567,
        "longitude": 118.1234,
        "rating": 4.6,
        "telephone": "0592-1234567",
        "distance": 850,
        "provider": "baidu",
    }
    values.update(overrides)
    return HotelSummary(**values)


def test_hotel_search_request_strips_city_and_uses_defaults() -> None:
    request = HotelSearchRequest(city=" 厦门 ")

    assert request.city == "厦门"
    assert request.keyword == "酒店"
    assert request.page == 1
    assert request.page_size == 10


def test_hotel_search_request_strips_keyword() -> None:
    request = HotelSearchRequest(city="厦门", keyword=" 海景酒店 ")

    assert request.keyword == "海景酒店"


def test_blank_hotel_search_keyword_falls_back_to_hotel() -> None:
    assert HotelSearchRequest(city="厦门", keyword="   ").keyword == "酒店"


@pytest.mark.parametrize("city", ["", "   "])
def test_hotel_search_request_rejects_blank_city(city: str) -> None:
    with pytest.raises(ValidationError):
        HotelSearchRequest(city=city)


@pytest.mark.parametrize("page", [0, -1])
def test_hotel_search_request_rejects_non_positive_page(page: int) -> None:
    with pytest.raises(ValidationError):
        HotelSearchRequest(city="厦门", page=page)


@pytest.mark.parametrize("page_size", [0, 21])
def test_hotel_search_request_rejects_page_size_outside_bounds(page_size: int) -> None:
    with pytest.raises(ValidationError):
        HotelSearchRequest(city="厦门", page_size=page_size)


def test_hotel_search_request_rejects_provider_fields() -> None:
    with pytest.raises(ValidationError):
        HotelSearchRequest(city="厦门", page_num=0)


def test_hotel_nearby_search_request_accepts_coordinates_and_defaults() -> None:
    request = HotelNearbySearchRequest(latitude=24.4798, longitude=118.0894)

    assert request.latitude == 24.4798
    assert request.longitude == 118.0894
    assert request.radius == 2000
    assert request.keyword == "酒店"
    assert request.page == 1
    assert request.page_size == 10


@pytest.mark.parametrize("latitude", [-90.1, 90.1, float("nan"), float("inf"), float("-inf")])
def test_hotel_nearby_search_request_rejects_invalid_latitude(latitude: float) -> None:
    with pytest.raises(ValidationError):
        HotelNearbySearchRequest(latitude=latitude, longitude=118.0894)


@pytest.mark.parametrize("longitude", [-180.1, 180.1, float("nan"), float("inf"), float("-inf")])
def test_hotel_nearby_search_request_rejects_invalid_longitude(longitude: float) -> None:
    with pytest.raises(ValidationError):
        HotelNearbySearchRequest(latitude=24.4798, longitude=longitude)


@pytest.mark.parametrize("radius", [0, -1, 20_001])
def test_hotel_nearby_search_request_rejects_invalid_radius(radius: int) -> None:
    with pytest.raises(ValidationError):
        HotelNearbySearchRequest(latitude=24.4798, longitude=118.0894, radius=radius)


def test_hotel_nearby_search_request_uses_same_keyword_and_pagination_rules() -> None:
    request = HotelNearbySearchRequest(
        latitude=24.4798,
        longitude=118.0894,
        keyword=" 海景酒店 ",
        page=2,
        page_size=20,
    )

    assert request.keyword == "海景酒店"
    assert request.page == 2
    assert request.page_size == 20


def test_hotel_nearby_search_request_rejects_provider_fields() -> None:
    with pytest.raises(ValidationError):
        HotelNearbySearchRequest(
            latitude=24.4798,
            longitude=118.0894,
            ret_coordtype="gcj02ll",
        )


def test_hotel_summary_accepts_minimal_object() -> None:
    hotel = HotelSummary(id="hotel-1", name="酒店", provider="baidu")

    assert hotel.address is None
    assert hotel.latitude is None
    assert hotel.rating is None
    assert hotel.distance is None


def test_hotel_summary_accepts_complete_object() -> None:
    hotel = summary()

    assert hotel.provider == "baidu"
    assert hotel.distance == 850


@pytest.mark.parametrize("field", ["id", "name"])
def test_hotel_summary_rejects_blank_required_text(field: str) -> None:
    with pytest.raises(ValidationError):
        summary(**{field: "   "})


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("latitude", float("nan")),
        ("latitude", float("inf")),
        ("longitude", float("-inf")),
        ("rating", float("nan")),
    ],
)
def test_hotel_summary_rejects_non_finite_numeric_values(field: str, value: float) -> None:
    with pytest.raises(ValidationError):
        summary(**{field: value})


@pytest.mark.parametrize("distance", [-1, -100])
def test_hotel_summary_rejects_negative_distance(distance: int) -> None:
    with pytest.raises(ValidationError):
        summary(distance=distance)


def test_hotel_summary_allows_other_provider_names() -> None:
    assert summary(provider="ctrip").provider == "ctrip"


def test_hotel_detail_inherits_summary_and_accepts_optional_fields() -> None:
    hotel = HotelDetail(
        **summary().model_dump(),
        tags=["海景", "亲子"],
        business_hours="全天",
        description="靠近海边",
        detail_url="https://example.com/hotel-1",
    )

    assert hotel.id == "baidu-hotel-1"
    assert hotel.tags == ["海景", "亲子"]
    assert hotel.detail_url == "https://example.com/hotel-1"


def test_hotel_detail_tags_default_is_not_shared() -> None:
    first = HotelDetail(id="hotel-1", name="酒店", provider="baidu")
    second = HotelDetail(id="hotel-2", name="酒店", provider="baidu")

    first.tags.append("海景")

    assert second.tags == []


def test_hotel_detail_removes_blank_tags() -> None:
    hotel = HotelDetail(
        id="hotel-1",
        name="酒店",
        provider="baidu",
        tags=["海景", "   ", "", "亲子"],
    )

    assert hotel.tags == ["海景", "亲子"]


def test_hotel_detail_allows_optional_fields_to_be_none() -> None:
    hotel = HotelDetail(
        id="hotel-1",
        name="酒店",
        provider="baidu",
        tags=[],
        business_hours=None,
        description=None,
        detail_url=None,
    )

    assert hotel.business_hours is None
    assert hotel.description is None
    assert hotel.detail_url is None


def test_hotel_search_result_accepts_success_and_empty_results() -> None:
    result = HotelSearchResult(
        items=[summary()],
        total=1,
        page=1,
        page_size=10,
        provider="baidu",
        status="success",
        warning=None,
        fetched_at=FETCHED_AT,
    )
    empty = HotelSearchResult(
        items=[],
        total=None,
        page=1,
        page_size=10,
        provider="baidu",
        status="unavailable",
        warning="暂时无法查询酒店",
        fetched_at=FETCHED_AT,
    )

    assert result.items[0].name == "厦门海景酒店"
    assert empty.total is None


def test_hotel_search_result_items_default_is_not_shared() -> None:
    values = {
        "page": 1,
        "page_size": 10,
        "provider": "baidu",
        "status": "success",
        "warning": None,
        "fetched_at": FETCHED_AT,
    }
    first = HotelSearchResult(**values)
    second = HotelSearchResult(**values)

    first.items.append(summary())

    assert second.items == []


@pytest.mark.parametrize("total", [-1, -10])
def test_hotel_search_result_rejects_negative_total(total: int) -> None:
    with pytest.raises(ValidationError):
        HotelSearchResult(
            total=total,
            page=1,
            page_size=10,
            provider="baidu",
            status="success",
            fetched_at=FETCHED_AT,
        )


def test_hotel_search_result_rejects_invalid_status() -> None:
    with pytest.raises(ValidationError):
        HotelSearchResult(
            page=1,
            page_size=10,
            provider="baidu",
            status="partial",
            fetched_at=FETCHED_AT,
        )


@pytest.mark.parametrize("field,value", [("page", 0), ("page_size", 0), ("page_size", 21)])
def test_hotel_search_result_validates_page_and_page_size(field: str, value: int) -> None:
    values = {
        "page": 1,
        "page_size": 10,
        "provider": "baidu",
        "status": "success",
        "fetched_at": FETCHED_AT,
    }
    values[field] = value

    with pytest.raises(ValidationError):
        HotelSearchResult(**values)


def test_hotel_search_result_requires_datetime_fetched_at() -> None:
    with pytest.raises(ValidationError):
        HotelSearchResult(
            page=1,
            page_size=10,
            provider="baidu",
            status="success",
            fetched_at="not-a-datetime",
        )
