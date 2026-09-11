from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from pydantic import SecretStr

from app.attractions.models import (
    AttractionNearbySearchRequest,
    AttractionSearchRequest,
)
from tests.fixtures.providers import RecordingTransport, json_response


REAL_TEST_AK = "REAL_TEST_AK"
TEST_CATEGORY_FILTER = "test_category_filter"
TEST_SORT_FILTERS = {
    "rating": "test_rating_sort",
    "distance": "test_distance_sort",
}


def provider_type():
    from app.providers.baidu_attraction import BaiduAttractionProvider

    return BaiduAttractionProvider


def provider_error_type():
    from app.providers.baidu_attraction import BaiduAttractionProviderError

    return BaiduAttractionProviderError


def provider_for(
    transport: RecordingTransport,
    *,
    api_key: str | SecretStr | None = REAL_TEST_AK,
    category_filter: str | None = TEST_CATEGORY_FILTER,
    sort_filters: dict[str, str] | None = TEST_SORT_FILTERS,
    timeout: float = 2.5,
):
    return provider_type()(
        api_key=api_key,
        client=httpx.Client(transport=transport),
        timeout=timeout,
        category_filter=category_filter,
        sort_filters=sort_filters,
    )


def attraction_result(
    *,
    uid: str | None = "baidu-attraction-1",
    name: str = "厦门大学",
    detail_info: dict[str, object] | None = None,
    **extra: object,
) -> dict[str, object]:
    result: dict[str, object] = {
        "name": name,
        "address": "厦门市思明区",
        "location": {"lat": 24.44, "lng": 118.08},
    }
    if uid is not None:
        result["uid"] = uid
    if detail_info is not None:
        result["detail_info"] = detail_info
    result.update(extra)
    return result


def search_payload(
    results: list[dict[str, object]],
    *,
    total: object = 1,
    include_total: bool = True,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "status": 0,
        "message": "ok",
        "results": results,
    }
    if include_total:
        payload["total"] = total
    return payload


def request_query(transport: RecordingTransport) -> dict[str, list[str]]:
    return parse_qs(urlparse(str(transport.requests[0].url)).query)


def test_blank_api_key_returns_not_configured_without_http() -> None:
    transport = RecordingTransport([])
    provider = provider_for(transport, api_key=" ")

    result = provider.search(AttractionSearchRequest(city="厦门"))

    assert result.status == "unavailable"
    assert result.warning == "BAIDU_ATTRACTION_NOT_CONFIGURED"
    assert transport.requests == []


def test_explicit_unverified_contract_returns_not_configured_without_http() -> None:
    transport = RecordingTransport([])
    provider = provider_type()(
        api_key=REAL_TEST_AK,
        client=httpx.Client(transport=transport),
        contract_state="unverified",
    )

    result = provider.search(AttractionSearchRequest(city="厦门"))

    assert provider.contract_state == "unverified"
    assert result.items == []
    assert result.total is None
    assert result.status == "unavailable"
    assert result.warning == "BAIDU_ATTRACTION_NOT_CONFIGURED"
    assert transport.requests == []


def test_missing_api_key_returns_not_configured_without_http() -> None:
    transport = RecordingTransport([])
    provider = provider_type()(
        api_key=None,
        client=httpx.Client(transport=transport),
    )

    result = provider.search(AttractionSearchRequest(city="厦门"))

    assert result.status == "unavailable"
    assert result.warning == "BAIDU_ATTRACTION_NOT_CONFIGURED"
    assert transport.requests == []


def test_default_verified_live_contract_allows_city_search() -> None:
    transport = RecordingTransport([json_response(search_payload([]))])
    provider = provider_type()(
        api_key=REAL_TEST_AK,
        client=httpx.Client(transport=transport),
    )

    result = provider.search(AttractionSearchRequest(city="厦门"))

    assert len(transport.requests) == 1
    assert result.status == "success"
    assert result.warning is None
    query = request_query(transport)
    assert query["query"] == ["景点"]
    assert query["region"] == ["厦门"]


def test_default_verified_live_contract_allows_nearby_search() -> None:
    transport = RecordingTransport([json_response(search_payload([], total=0))])
    provider = provider_type()(
        api_key=REAL_TEST_AK,
        client=httpx.Client(transport=transport),
    )

    result = provider.search(
        AttractionNearbySearchRequest(
            latitude=24.4798,
            longitude=118.0894,
        )
    )

    assert len(transport.requests) == 1
    assert result.status == "success"
    assert result.warning is None
    request = transport.requests[0]
    assert urlparse(str(request.url)).path == "/place/v3/around"
    query = request_query(transport)
    assert query["query"] == ["景点"]
    assert query["radius"] == ["2000"]


def test_incomplete_sort_contract_is_unverified_without_http() -> None:
    transport = RecordingTransport([])
    provider = provider_for(transport, sort_filters={"rating": "test_rating_sort"})

    result = provider.search(
        AttractionNearbySearchRequest(latitude=24.44, longitude=118.08)
    )

    assert provider.contract_state == "unverified"
    assert result.status == "unavailable"
    assert result.warning == "BAIDU_ATTRACTION_NOT_CONFIGURED"
    assert transport.requests == []


def test_city_rating_request_uses_live_sort_filter_contract() -> None:
    transport = RecordingTransport([json_response(search_payload([]))])
    provider = provider_for(
        transport,
        category_filter="industry_type:life",
        sort_filters=None,
    )

    provider.search(AttractionSearchRequest(city="厦门", sort_by="rating"))

    actual_filter = (
        request_query(transport)["filter"][0] if transport.requests else None
    )
    assert actual_filter == (
        "industry_type:life|sort_name:overall_rating|sort_rule:0"
    )


def test_nearby_distance_request_uses_live_sort_filter_contract() -> None:
    transport = RecordingTransport([json_response(search_payload([], total=0))])
    provider = provider_for(
        transport,
        category_filter="industry_type:life",
        sort_filters=None,
    )

    provider.search(
        AttractionNearbySearchRequest(
            latitude=24.44,
            longitude=118.08,
            sort_by="distance",
        )
    )

    actual_filter = (
        request_query(transport)["filter"][0] if transport.requests else None
    )
    assert actual_filter == "industry_type:life|sort_name:distance|sort_rule:1"


def test_city_search_uses_region_endpoint_and_controlled_filter() -> None:
    transport = RecordingTransport([json_response(search_payload([]))])
    provider = provider_for(transport)

    provider.search(
        AttractionSearchRequest(city="厦门", page=2, page_size=3, sort_by="rating")
    )

    request = transport.requests[0]
    assert urlparse(str(request.url)).path == "/place/v3/region"
    assert request_query(transport) == {
        "query": ["景点"],
        "region": ["厦门"],
        "region_limit": ["true"],
        "scope": ["2"],
        "page_num": ["1"],
        "page_size": ["10"],
        "filter": ["test_category_filter|test_rating_sort"],
        "ret_coordtype": ["gcj02ll"],
        "output": ["json"],
        "ak": [REAL_TEST_AK],
    }


def test_city_search_uses_default_keyword_and_no_user_filter() -> None:
    transport = RecordingTransport([json_response(search_payload([]))])
    provider = provider_for(transport)

    provider.search(AttractionSearchRequest(city="厦门", keyword="user-filter"))

    query = request_query(transport)
    assert query["query"] == ["user-filter"]
    assert query["filter"] == [TEST_CATEGORY_FILTER]
    assert "user-filter" not in query["filter"][0]


def test_nearby_search_uses_around_endpoint_and_coordinates() -> None:
    transport = RecordingTransport([json_response(search_payload([] , total=0))])
    provider = provider_for(transport)

    result = provider.search(
        AttractionNearbySearchRequest(
            latitude=24.4798,
            longitude=118.0894,
            radius=3000,
            page=2,
            page_size=10,
            sort_by="distance",
        )
    )

    request = transport.requests[0]
    assert urlparse(str(request.url)).path == "/place/v3/around"
    assert request_query(transport) == {
        "query": ["景点"],
        "location": ["24.4798,118.0894"],
        "radius": ["3000"],
        "radius_limit": ["true"],
        "coord_type": ["2"],
        "scope": ["2"],
        "page_num": ["1"],
        "page_size": ["10"],
        "filter": ["test_category_filter|test_distance_sort"],
        "ret_coordtype": ["gcj02ll"],
        "output": ["json"],
        "ak": [REAL_TEST_AK],
    }
    assert result.status == "success"


@pytest.mark.parametrize(
    ("sort_by", "expected_filter"),
    [
        ("rating", "test_category_filter|test_rating_sort"),
        ("distance", "test_category_filter|test_distance_sort"),
    ],
)
def test_nearby_search_uses_injected_sort_filter(
    sort_by: str,
    expected_filter: str,
) -> None:
    transport = RecordingTransport([json_response(search_payload([] , total=0))])
    provider = provider_for(transport)

    provider.search(
        AttractionNearbySearchRequest(
            latitude=24.44,
            longitude=118.08,
            sort_by=sort_by,
        )
    )

    assert request_query(transport)["filter"] == [expected_filter]


def test_provider_parses_optional_fields_and_tags() -> None:
    payload = search_payload(
        [
            attraction_result(
                detail_info={
                    "overall_rating": "4.8",
                    "comment_num": "1234",
                    "distance": "320",
                    "tag": "景区, 亲子|自然",
                    "classified_poi_tag": "自然;景区",
                }
            )
        ]
    )
    transport = RecordingTransport([json_response(payload)])

    result = provider_for(transport).search(AttractionSearchRequest(city="厦门"))

    item = result.items[0]
    assert item.id == "baidu-attraction-1"
    assert item.name == "厦门大学"
    assert item.address == "厦门市思明区"
    assert item.latitude == 24.44
    assert item.longitude == 118.08
    assert item.rating == 4.8
    assert item.comment_num == 1234
    assert item.distance == 320
    assert item.tags == ("景区", "亲子", "自然")
    assert item.provider == "baidu"


def test_provider_keeps_missing_optional_fields_as_none() -> None:
    transport = RecordingTransport(
        [json_response(search_payload([attraction_result(detail_info={})]))]
    )

    item = provider_for(transport).search(AttractionSearchRequest(city="厦门")).items[0]

    assert item.rating is None
    assert item.comment_num is None
    assert item.distance is None
    assert item.tags == ()


def test_invalid_optional_fields_do_not_discard_valid_attraction() -> None:
    payload = search_payload(
        [
            attraction_result(
                detail_info={
                    "overall_rating": "unknown",
                    "comment_num": "unknown",
                    "distance": "12.5",
                },
                location={"lat": "bad", "lng": 200},
            )
        ]
    )
    transport = RecordingTransport([json_response(payload)])

    result = provider_for(transport).search(AttractionSearchRequest(city="厦门"))

    assert len(result.items) == 1
    item = result.items[0]
    assert item.rating is None
    assert item.comment_num is None
    assert item.distance is None
    assert item.latitude is None
    assert item.longitude is None


def test_missing_uid_still_produces_attraction_with_none_id() -> None:
    transport = RecordingTransport(
        [json_response(search_payload([attraction_result(uid=None)]))]
    )

    item = provider_for(transport).search(AttractionSearchRequest(city="厦门")).items[0]

    assert item.id is None
    assert item.name == "厦门大学"


def test_missing_name_skips_only_that_result() -> None:
    transport = RecordingTransport(
        [
            json_response(
                search_payload(
                    [
                        attraction_result(uid="missing-name", name=""),
                        attraction_result(uid="valid", name="鼓浪屿"),
                    ]
                )
            )
        ]
    )

    result = provider_for(transport).search(AttractionSearchRequest(city="厦门"))

    assert [item.id for item in result.items] == ["valid"]


def test_missing_total_is_preserved_as_none() -> None:
    transport = RecordingTransport(
        [json_response(search_payload([], include_total=False))]
    )

    result = provider_for(transport).search(AttractionSearchRequest(city="厦门"))

    assert result.status == "success"
    assert result.total is None


def test_empty_search_results_are_successful() -> None:
    transport = RecordingTransport([json_response(search_payload([], total=0))])

    result = provider_for(transport).search(AttractionSearchRequest(city="厦门"))

    assert result.status == "success"
    assert result.items == []
    assert result.total == 0


def test_small_domain_page_size_uses_upstream_minimum_and_trims_locally() -> None:
    payload = search_payload(
        [attraction_result(uid=f"attraction-{index}", name=f"景点 {index}") for index in range(4)],
        total=24,
    )
    transport = RecordingTransport([json_response(payload)])

    result = provider_for(transport).search(
        AttractionSearchRequest(city="厦门", page=2, page_size=3)
    )

    assert request_query(transport)["page_size"] == ["10"]
    assert [item.id for item in result.items] == [
        "attraction-0",
        "attraction-1",
        "attraction-2",
    ]


def test_provider_preserves_upstream_order_without_local_sort() -> None:
    payload = search_payload(
        [
            attraction_result(uid="a", name="A", detail_info={"overall_rating": "3"}),
            attraction_result(uid="b", name="B", detail_info={"overall_rating": "5"}),
            attraction_result(uid="c", name="C", detail_info={"overall_rating": "4"}),
        ]
    )
    transport = RecordingTransport([json_response(payload)])

    result = provider_for(transport).search(AttractionSearchRequest(city="厦门"))

    assert [item.id for item in result.items] == ["a", "b", "c"]


@pytest.mark.parametrize(
    ("response", "expected_code"),
    [
        (json_response({"status": 1, "message": "bad ak"}), "BAIDU_ATTRACTION_PROVIDER_ERROR"),
        (json_response({"status": 0}, status_code=503), "BAIDU_ATTRACTION_HTTP_ERROR"),
        (httpx.ReadTimeout("timed out"), "BAIDU_ATTRACTION_TIMEOUT"),
        (httpx.ConnectError("network down"), "BAIDU_ATTRACTION_NETWORK_ERROR"),
    ],
)
def test_provider_failures_raise_sanitized_errors(
    response: httpx.Response | Exception,
    expected_code: str,
) -> None:
    transport = RecordingTransport([response])

    with pytest.raises(provider_error_type()) as error:
        provider_for(transport).search(AttractionSearchRequest(city="厦门"))

    assert error.value.code == expected_code
    assert REAL_TEST_AK not in str(error.value)
    assert "bad ak" not in str(error.value)
    assert "network down" not in str(error.value)


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, content=b"<html>error</html>"),
        json_response({"status": 0, "results": "wrong"}),
    ],
)
def test_invalid_upstream_payload_raises_sanitized_error(response: httpx.Response) -> None:
    transport = RecordingTransport([response])

    with pytest.raises(provider_error_type()) as error:
        provider_for(transport).search(AttractionSearchRequest(city="厦门"))

    assert error.value.code == "BAIDU_ATTRACTION_INVALID_RESPONSE"
    assert REAL_TEST_AK not in str(error.value)


def test_malformed_poi_entries_are_skipped_without_discarding_valid_entries() -> None:
    transport = RecordingTransport(
        [
            json_response(
                search_payload(
                    ["wrong", attraction_result(uid="valid", name="鼓浪屿")]
                )
            )
        ]
    )

    result = provider_for(transport).search(AttractionSearchRequest(city="厦门"))

    assert [item.id for item in result.items] == ["valid"]


def test_invalid_request_raises_sanitized_error_without_http() -> None:
    transport = RecordingTransport([])

    with pytest.raises(provider_error_type()) as error:
        provider_for(transport).search(object())

    assert error.value.code == "BAIDU_ATTRACTION_INVALID_REQUEST"
    assert transport.requests == []


def test_timeout_must_be_positive_and_finite() -> None:
    with pytest.raises(ValueError):
        provider_type()(
            api_key=REAL_TEST_AK,
            timeout=0,
            category_filter=TEST_CATEGORY_FILTER,
            sort_filters=TEST_SORT_FILTERS,
        )


def test_provider_creates_http_client_without_trusting_environment(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class CapturingClient:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

    monkeypatch.setattr(httpx, "Client", CapturingClient)

    provider_type()(
        api_key=REAL_TEST_AK,
        category_filter=TEST_CATEGORY_FILTER,
        sort_filters=TEST_SORT_FILTERS,
    )

    assert captured["trust_env"] is False
