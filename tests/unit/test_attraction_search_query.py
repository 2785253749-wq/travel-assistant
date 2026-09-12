import pytest


def _extractor_type():
    from app.agent.attraction_search_query import AttractionSearchQueryExtractor

    return AttractionSearchQueryExtractor


def test_extracts_basic_city_attraction_query() -> None:
    extractor = _extractor_type()()

    result = extractor.extract("厦门有哪些景点")

    assert result.mode == "city"
    assert result.city == "厦门"
    assert result.location_query is None
    assert result.radius is None
    assert result.sort_by is None
    assert result.invalid_fields == ()
    assert result.missing_fields == ()


def test_extracts_city_fun_attraction_query() -> None:
    extractor = _extractor_type()()

    result = extractor.extract("厦门有什么好玩的景点")

    assert result.mode == "city"
    assert result.city == "厦门"


def test_extracts_city_rating_query() -> None:
    extractor = _extractor_type()()

    result = extractor.extract("厦门评分最高的景点")

    assert result.mode == "city"
    assert result.city == "厦门"
    assert result.sort_by == "rating"
    assert result.invalid_fields == ()


@pytest.mark.parametrize(
    ("message", "city", "sort_by"),
    [
        ("福州有哪些景点", "福州", None),
        ("杭州评分最高的景点", "杭州", "rating"),
        ("北京有哪些景点", "北京", None),
        ("成都评分最高的景点", "成都", "rating"),
    ],
)
def test_extracts_cross_region_city_attraction_queries(
    message: str,
    city: str,
    sort_by: str | None,
) -> None:
    extractor = _extractor_type()()

    result = extractor.extract(message)

    assert result.mode == "city"
    assert result.city == city
    assert result.location_query is None
    assert result.sort_by == sort_by
    assert result.missing_fields == ()


@pytest.mark.parametrize(
    ("message", "city", "location_query", "sort_by"),
    [
        ("北京大学附近有什么景点", "北京", "北京大学", None),
        ("杭州西湖附近最近的景点", "杭州", "杭州西湖", "distance"),
        (
            "成都宽窄巷子附近评分最高的景点",
            "成都",
            "成都宽窄巷子",
            "rating",
        ),
        ("福州三坊七巷附近最近的景点", "福州", "福州三坊七巷", "distance"),
    ],
)
def test_extracts_cross_region_nearby_attraction_queries(
    message: str,
    city: str,
    location_query: str,
    sort_by: str | None,
) -> None:
    extractor = _extractor_type()()

    result = extractor.extract(message)

    assert result.mode == "nearby"
    assert result.city == city
    assert result.location_query == location_query
    assert result.sort_by == sort_by
    assert result.missing_fields == ()


def test_extracts_cross_region_city_only_nearby_word() -> None:
    extractor = _extractor_type()()

    result = extractor.extract("北京附近有什么景点")

    assert result.mode == "city"
    assert result.city == "北京"
    assert result.location_query is None
    assert result.missing_fields == ()


def test_extracts_nearby_attraction_query_with_full_poi_name() -> None:
    extractor = _extractor_type()()

    result = extractor.extract("厦门大学附近有什么景点")

    assert result.mode == "nearby"
    assert result.city == "厦门"
    assert result.location_query == "厦门大学"
    assert result.sort_by is None
    assert result.missing_fields == ()


def test_extracts_nearby_recommendation_without_city_alias() -> None:
    extractor = _extractor_type()()

    result = extractor.extract("厦门鼓浪屿附近景点推荐")

    assert result.mode == "nearby"
    assert result.city == "厦门"
    assert result.location_query == "厦门鼓浪屿"


def test_extracts_nearby_rating_query_without_polluting_location() -> None:
    extractor = _extractor_type()()

    result = extractor.extract("厦门大学附近评分最高的景点")

    assert result.mode == "nearby"
    assert result.city == "厦门"
    assert result.location_query == "厦门大学"
    assert result.sort_by == "rating"


def test_extracts_nearby_distance_query() -> None:
    extractor = _extractor_type()()

    result = extractor.extract("厦门大学附近最近的景点")

    assert result.mode == "nearby"
    assert result.location_query == "厦门大学"
    assert result.sort_by == "distance"


def test_extracts_nearby_radius_and_rating() -> None:
    extractor = _extractor_type()()

    result = extractor.extract("厦门大学附近3公里评分最高的景点")

    assert result.mode == "nearby"
    assert result.location_query == "厦门大学"
    assert result.radius == 3000
    assert result.sort_by == "rating"
    assert result.invalid_fields == ()


def test_missing_city_is_structured_for_nearby_poi() -> None:
    extractor = _extractor_type()()

    result = extractor.extract("鼓浪屿附近有什么景点")

    assert result.mode == "nearby"
    assert result.city is None
    assert result.location_query == "鼓浪屿"
    assert result.missing_fields == ("city",)


def test_city_only_nearby_word_does_not_create_nearby_location() -> None:
    extractor = _extractor_type()()

    result = extractor.extract("厦门附近有什么景点")

    assert result.mode == "city"
    assert result.city == "厦门"
    assert result.location_query is None
    assert result.missing_fields == ()


def test_city_distance_is_invalid_and_not_a_valid_city_request() -> None:
    extractor = _extractor_type()()

    result = extractor.extract("厦门最近的景点")

    assert result.mode == "city"
    assert result.city == "厦门"
    assert result.sort_by == "distance"
    assert result.invalid_fields == ("sort_by",)


@pytest.mark.parametrize(
    ("message", "radius"),
    [
        ("厦门大学附近500米的景点", 500),
        ("厦门大学附近20000米的景点", 20_000),
        ("厦门大学附近一公里的景点", 1000),
        ("厦门大学附近两公里的景点", 2000),
    ],
)
def test_extracts_supported_radius_boundaries_and_chinese_units(
    message: str,
    radius: int,
) -> None:
    extractor = _extractor_type()()

    result = extractor.extract(message)

    assert result.radius == radius
    assert result.invalid_fields == ()


@pytest.mark.parametrize(
    ("message", "radius"),
    [
        ("厦门大学附近100米的景点", 100),
        ("厦门大学附近21公里的景点", 21_000),
    ],
)
def test_marks_invalid_radius_without_clamping(message: str, radius: int) -> None:
    extractor = _extractor_type()()

    result = extractor.extract(message)

    assert result.radius == radius
    assert result.invalid_fields == ("radius",)


def test_sort_terms_do_not_contaminate_location_query() -> None:
    extractor = _extractor_type()()

    result = extractor.extract("厦门大学附近离这里最近的景点")

    assert result.location_query == "厦门大学"
    assert "最近" not in result.location_query
    assert result.sort_by == "distance"


def test_radius_terms_do_not_contaminate_location_query() -> None:
    extractor = _extractor_type()()

    result = extractor.extract("厦门大学附近3公里的景点")

    assert result.location_query == "厦门大学"
    assert "3公里" not in result.location_query
    assert result.radius == 3000


def test_normalizes_message_whitespace() -> None:
    extractor = _extractor_type()()

    result = extractor.extract("  厦门大学附近   景点推荐  ")

    assert result.mode == "nearby"
    assert result.location_query == "厦门大学"
    assert result.city == "厦门"


def test_attraction_price_words_do_not_create_unsupported_sort() -> None:
    extractor = _extractor_type()()

    result = extractor.extract("厦门最便宜的景点")

    assert result.mode == "city"
    assert result.city == "厦门"
    assert result.sort_by is None
    assert result.invalid_fields == ()


def test_irrelevant_message_returns_empty_extraction() -> None:
    extractor = _extractor_type()()

    result = extractor.extract("厦门明天天气怎么样")

    assert result.mode is None
    assert result.city is None
    assert result.location_query is None
    assert result.radius is None
    assert result.sort_by is None
    assert result.invalid_fields == ()
    assert result.missing_fields == ()
