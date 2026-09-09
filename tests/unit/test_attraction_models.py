from datetime import UTC, datetime
from math import inf, nan

import pytest
from pydantic import ValidationError


FETCHED_AT = datetime(2026, 9, 9, 12, 0, tzinfo=UTC)


def _models():
    from app.attractions.models import (
        AttractionNearbySearchRequest,
        AttractionSearchRequest,
        AttractionSearchResult,
        AttractionSummary,
    )

    return (
        AttractionNearbySearchRequest,
        AttractionSearchRequest,
        AttractionSearchResult,
        AttractionSummary,
    )


def _summary_values() -> dict[str, object]:
    return {
        "id": "baidu-attraction-1",
        "name": "厦门大学",
        "address": "厦门市思明区",
        "latitude": 24.44,
        "longitude": 118.09,
        "rating": 4.8,
        "comment_num": 123,
        "distance": 320,
        "tags": ("校园景观",),
        "provider": "baidu",
    }


def _summary(**overrides: object) -> dict[str, object]:
    values = _summary_values()
    values.update(overrides)
    return values


def _result_values(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "items": [],
        "total": 0,
        "page": 1,
        "page_size": 10,
        "provider": "baidu",
        "status": "success",
        "warning": None,
        "fetched_at": FETCHED_AT,
    }
    values.update(overrides)
    return values


def test_city_request_uses_frozen_defaults() -> None:
    _, AttractionSearchRequest, _, _ = _models()

    request = AttractionSearchRequest(city="厦门")

    assert request.city == "厦门"
    assert request.keyword == "景点"
    assert request.page == 1
    assert request.page_size == 10
    assert request.sort_by is None


def test_city_request_accepts_rating_sort() -> None:
    _, AttractionSearchRequest, _, _ = _models()

    assert AttractionSearchRequest(city="厦门", sort_by="rating").sort_by == "rating"


@pytest.mark.parametrize("sort_by", ["distance", "price"])
def test_city_request_rejects_non_city_sort(sort_by: str) -> None:
    _, AttractionSearchRequest, _, _ = _models()

    with pytest.raises(ValidationError):
        AttractionSearchRequest(city="厦门", sort_by=sort_by)


def test_city_request_rejects_blank_city() -> None:
    _, AttractionSearchRequest, _, _ = _models()

    with pytest.raises(ValidationError):
        AttractionSearchRequest(city="   ")


def test_city_request_normalizes_blank_keyword_to_attraction_keyword() -> None:
    _, AttractionSearchRequest, _, _ = _models()

    assert AttractionSearchRequest(city="厦门", keyword="  ").keyword == "景点"


@pytest.mark.parametrize("page", [0, -1])
def test_city_request_rejects_non_positive_page(page: int) -> None:
    _, AttractionSearchRequest, _, _ = _models()

    with pytest.raises(ValidationError):
        AttractionSearchRequest(city="厦门", page=page)


@pytest.mark.parametrize("page_size", [0, 21])
def test_city_request_rejects_page_size_outside_bounds(page_size: int) -> None:
    _, AttractionSearchRequest, _, _ = _models()

    with pytest.raises(ValidationError):
        AttractionSearchRequest(city="厦门", page_size=page_size)


def test_nearby_request_uses_frozen_defaults() -> None:
    NearbyRequest, _, _, _ = _models()

    request = NearbyRequest(latitude=24.44, longitude=118.09)

    assert request.radius == 2000
    assert request.keyword == "景点"
    assert request.page == 1
    assert request.page_size == 10
    assert request.sort_by is None


def test_nearby_request_accepts_rating_sort() -> None:
    NearbyRequest, _, _, _ = _models()

    assert NearbyRequest(latitude=24.44, longitude=118.09, sort_by="rating").sort_by == "rating"


def test_nearby_request_accepts_distance_sort() -> None:
    NearbyRequest, _, _, _ = _models()

    assert NearbyRequest(latitude=24.44, longitude=118.09, sort_by="distance").sort_by == "distance"


def test_nearby_request_rejects_price_sort() -> None:
    NearbyRequest, _, _, _ = _models()

    with pytest.raises(ValidationError):
        NearbyRequest(latitude=24.44, longitude=118.09, sort_by="price")


def test_nearby_request_rejects_latitude_outside_range() -> None:
    NearbyRequest, _, _, _ = _models()

    with pytest.raises(ValidationError):
        NearbyRequest(latitude=90.1, longitude=118.09)


def test_nearby_request_rejects_longitude_outside_range() -> None:
    NearbyRequest, _, _, _ = _models()

    with pytest.raises(ValidationError):
        NearbyRequest(latitude=24.44, longitude=180.1)


@pytest.mark.parametrize("value", [inf, -inf, nan])
def test_nearby_request_rejects_non_finite_coordinates(value: float) -> None:
    NearbyRequest, _, _, _ = _models()

    with pytest.raises(ValidationError):
        NearbyRequest(latitude=value, longitude=118.09)


@pytest.mark.parametrize("radius", [1, 20_000])
def test_nearby_request_accepts_radius_boundaries(radius: int) -> None:
    NearbyRequest, _, _, _ = _models()

    assert NearbyRequest(latitude=24.44, longitude=118.09, radius=radius).radius == radius


def test_nearby_request_rejects_radius_outside_bounds() -> None:
    NearbyRequest, _, _, _ = _models()

    with pytest.raises(ValidationError):
        NearbyRequest(latitude=24.44, longitude=118.09, radius=20_001)


def test_nearby_request_rejects_invalid_page_and_page_size() -> None:
    NearbyRequest, _, _, _ = _models()

    with pytest.raises(ValidationError):
        NearbyRequest(latitude=24.44, longitude=118.09, page=0)
    with pytest.raises(ValidationError):
        NearbyRequest(latitude=24.44, longitude=118.09, page_size=21)


def test_attraction_summary_accepts_minimal_valid_object() -> None:
    _, _, _, AttractionSummary = _models()

    item = AttractionSummary(name="厦门大学", provider="baidu")

    assert item.name == "厦门大学"
    assert item.provider == "baidu"
    assert item.tags == ()


def test_attraction_summary_allows_optional_fields_to_be_none() -> None:
    _, _, _, AttractionSummary = _models()

    item = AttractionSummary(
        name="厦门大学",
        provider="baidu",
        id=None,
        address=None,
        latitude=None,
        longitude=None,
        rating=None,
        comment_num=None,
        distance=None,
    )

    assert item.id is None
    assert item.distance is None


@pytest.mark.parametrize("field", ["rating", "comment_num", "distance"])
def test_attraction_summary_rejects_negative_numeric_fields(field: str) -> None:
    _, _, _, AttractionSummary = _models()

    with pytest.raises(ValidationError):
        AttractionSummary(**_summary(**{field: -1}))


def test_attraction_summary_keeps_distance_as_int() -> None:
    _, _, _, AttractionSummary = _models()

    item = AttractionSummary(**_summary(distance=320))

    assert isinstance(item.distance, int)
    assert not isinstance(item.distance, float)


def test_attraction_summary_cleans_tag_whitespace() -> None:
    _, _, _, AttractionSummary = _models()

    item = AttractionSummary(**_summary(tags=(" 景点 ", "历史建筑")))

    assert item.tags == ("景点", "历史建筑")


def test_attraction_summary_removes_empty_tags() -> None:
    _, _, _, AttractionSummary = _models()

    item = AttractionSummary(**_summary(tags=("", "  ", "景点")))

    assert item.tags == ("景点",)


@pytest.mark.parametrize("field", ["id", "address"])
def test_attraction_summary_normalizes_blank_optional_text(field: str) -> None:
    _, _, _, AttractionSummary = _models()

    item = AttractionSummary(**_summary(**{field: "   "}))

    assert getattr(item, field) is None


@pytest.mark.parametrize("field", ["name", "provider"])
def test_attraction_summary_rejects_blank_required_text(field: str) -> None:
    _, _, _, AttractionSummary = _models()

    with pytest.raises(ValidationError):
        AttractionSummary(**_summary(**{field: "   "}))


def test_result_allows_missing_total() -> None:
    _, _, AttractionSearchResult, _ = _models()

    result = AttractionSearchResult(**_result_values(total=None))

    assert result.total is None


def test_result_rejects_negative_total() -> None:
    _, _, AttractionSearchResult, _ = _models()

    with pytest.raises(ValidationError):
        AttractionSearchResult(**_result_values(total=-1))


def test_result_rejects_unknown_status() -> None:
    _, _, AttractionSearchResult, _ = _models()

    with pytest.raises(ValidationError):
        AttractionSearchResult(**_result_values(status="partial"))


def test_result_validates_page_and_page_size() -> None:
    _, _, AttractionSearchResult, _ = _models()

    with pytest.raises(ValidationError):
        AttractionSearchResult(**_result_values(page=0))
    with pytest.raises(ValidationError):
        AttractionSearchResult(**_result_values(page_size=21))
