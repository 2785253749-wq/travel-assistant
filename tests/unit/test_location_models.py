import pytest
from pydantic import ValidationError

from app.locations.models import (
    LocationCandidate,
    LocationQuery,
    LocationSearchResult,
)


def test_location_query_strips_query_and_city() -> None:
    query = LocationQuery(query="  厦门大学  ", city="  厦门  ")

    assert query.query == "厦门大学"
    assert query.city == "厦门"


def test_location_query_allows_missing_city() -> None:
    query = LocationQuery(query="鼓浪屿")

    assert query.city is None


def test_location_query_normalizes_blank_city_to_none() -> None:
    query = LocationQuery(query="泉州站", city="   ")

    assert query.city is None


@pytest.mark.parametrize("query", ["", "   ", "\t", "\n"])
def test_location_query_rejects_blank_query(query: str) -> None:
    with pytest.raises(ValidationError):
        LocationQuery(query=query)


def candidate(**overrides) -> LocationCandidate:
    values = {
        "id": "baidu-poi-1",
        "name": "厦门大学",
        "latitude": 24.4389,
        "longitude": 118.1019,
        "address": "厦门市思明区思明南路 422 号",
        "city": "厦门",
        "district": "思明区",
        "province": "福建省",
        "provider": "baidu",
    }
    values.update(overrides)
    return LocationCandidate(**values)


def test_location_candidate_strips_required_and_optional_text() -> None:
    location = candidate(
        id="  baidu-poi-1  ",
        name="  厦门大学  ",
        address="  厦门市思明区思明南路 422 号  ",
        city="  厦门  ",
        district="  思明区  ",
        province="  福建省  ",
        provider="  baidu  ",
    )

    assert location.id == "baidu-poi-1"
    assert location.name == "厦门大学"
    assert location.address == "厦门市思明区思明南路 422 号"
    assert location.city == "厦门"
    assert location.district == "思明区"
    assert location.province == "福建省"
    assert location.provider == "baidu"


def test_location_candidate_allows_missing_id_and_optional_text() -> None:
    location = candidate(
        id=None,
        address=None,
        city=None,
        district=None,
        province=None,
    )

    assert location.id is None
    assert location.address is None
    assert location.city is None
    assert location.district is None
    assert location.province is None


@pytest.mark.parametrize("field", ["name", "provider"])
def test_location_candidate_rejects_blank_required_text(field: str) -> None:
    with pytest.raises(ValidationError):
        candidate(**{field: "   "})


def test_location_candidate_normalizes_blank_id_to_none() -> None:
    assert candidate(id="   ").id is None


@pytest.mark.parametrize("field", ["address", "city", "district", "province"])
def test_location_candidate_normalizes_blank_optional_text_to_none(field: str) -> None:
    assert getattr(candidate(**{field: "   "}), field) is None


@pytest.mark.parametrize("latitude", [-90, 90])
def test_location_candidate_accepts_latitude_boundaries(latitude: float) -> None:
    assert candidate(latitude=latitude).latitude == latitude


@pytest.mark.parametrize("longitude", [-180, 180])
def test_location_candidate_accepts_longitude_boundaries(longitude: float) -> None:
    assert candidate(longitude=longitude).longitude == longitude


@pytest.mark.parametrize(
    "latitude",
    [-90.0001, 90.0001, float("nan"), float("inf"), float("-inf")],
)
def test_location_candidate_rejects_invalid_latitude(latitude: float) -> None:
    with pytest.raises(ValidationError):
        candidate(latitude=latitude)


@pytest.mark.parametrize(
    "longitude",
    [-180.0001, 180.0001, float("nan"), float("inf"), float("-inf")],
)
def test_location_candidate_rejects_invalid_longitude(longitude: float) -> None:
    with pytest.raises(ValidationError):
        candidate(longitude=longitude)


def test_location_candidate_allows_arbitrary_provider_names() -> None:
    assert candidate(provider="  amap  ").provider == "amap"


def test_location_candidate_rejects_provider_specific_fields() -> None:
    with pytest.raises(ValidationError):
        candidate(uid="baidu-uid-1")


def test_location_search_result_accepts_candidates_and_strips_provider() -> None:
    result = LocationSearchResult(items=[candidate()], provider="  baidu  ")

    assert result.items == [candidate()]
    assert result.provider == "baidu"


def test_location_search_result_defaults_to_empty_items() -> None:
    result = LocationSearchResult(provider="fake")

    assert result.items == []


def test_location_search_result_items_default_is_not_shared() -> None:
    first = LocationSearchResult(provider="fake")
    second = LocationSearchResult(provider="fake")

    first.items.append(candidate())

    assert second.items == []


def test_location_search_result_rejects_blank_provider() -> None:
    with pytest.raises(ValidationError):
        LocationSearchResult(provider="   ")
