from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from pydantic import SecretStr

from app.hotels.models import HotelNearbySearchRequest, HotelDetail, HotelSearchRequest
from app.providers.baidu_hotel import (
    BaiduHotelProvider,
    BaiduHotelProviderError,
)
from tests.fixtures.providers import RecordingTransport, json_response


REAL_TEST_AK = "REAL_TEST_AK"


def provider_for(
    transport: RecordingTransport,
    *,
    api_key: str | SecretStr | None = REAL_TEST_AK,
    timeout: float = 2.5,
) -> BaiduHotelProvider:
    return BaiduHotelProvider(
        api_key=api_key,
        client=httpx.Client(transport=transport),
        timeout=timeout,
    )


def hotel_result(
    *,
    uid: str = "baidu-1",
    name: str = "厦门海景酒店",
    detail_info: dict[str, object] | None = None,
    **extra: object,
) -> dict[str, object]:
    result: dict[str, object] = {
        "uid": uid,
        "name": name,
        "address": "环岛路 1 号",
        "location": {"lat": 24.44, "lng": 118.08},
        "telephone": "0592-1234567",
    }
    if detail_info is not None:
        result["detail_info"] = detail_info
    result.update(extra)
    return result


def search_payload(results: list[dict[str, object]], *, total: int = 1) -> dict[str, object]:
    return {
        "status": 0,
        "message": "ok",
        "total": total,
        "results": results,
    }


def test_missing_or_blank_api_key_is_rejected_without_http() -> None:
    transport = RecordingTransport([])

    with pytest.raises(BaiduHotelProviderError) as error:
        provider_for(transport, api_key=" ")

    assert error.value.code == "BAIDU_HOTEL_NOT_CONFIGURED"
    assert transport.requests == []


def test_city_search_uses_region_endpoint_and_maps_a_summary() -> None:
    payload = search_payload(
        [
            hotel_result(
                detail_info={"overall_rating": "4.8", "distance": "120"}
            )
        ]
    )
    transport = RecordingTransport([json_response(payload)])
    provider = provider_for(transport)

    result = provider.search(
        HotelSearchRequest(city="厦门", keyword="酒店", page=1, page_size=10)
    )

    request = transport.requests[0]
    assert urlparse(str(request.url)).path == "/place/v3/region"
    assert parse_qs(urlparse(str(request.url)).query) == {
        "query": ["酒店"],
        "region": ["厦门"],
        "region_limit": ["true"],
        "scope": ["2"],
        "page_num": ["0"],
        "page_size": ["10"],
        "filter": ["industry_type:hotel"],
        "ret_coordtype": ["gcj02ll"],
        "output": ["json"],
        "ak": [REAL_TEST_AK],
    }
    assert request.extensions["timeout"]["read"] == 2.5
    assert result.status == "success"
    assert result.total == 1
    assert result.items[0].id == "baidu-1"
    assert result.items[0].latitude == 24.44
    assert result.items[0].longitude == 118.08
    assert result.items[0].rating == 4.8
    assert result.items[0].telephone == "0592-1234567"
    assert result.items[0].distance == 120
    assert result.items[0].provider == "baidu"
    assert result.page == 1
    assert result.page_size == 10
    assert result.fetched_at.tzinfo is not None


def test_page_is_converted_and_small_domain_page_size_is_trimmed_locally() -> None:
    payload = search_payload(
        [hotel_result(uid=f"baidu-{index}", name=f"酒店 {index}") for index in range(4)],
        total=24,
    )
    transport = RecordingTransport([json_response(payload)])
    provider = provider_for(transport)

    result = provider.search(
        HotelSearchRequest(city="厦门", page=2, page_size=3)
    )

    query = parse_qs(urlparse(str(transport.requests[0].url)).query)
    assert query["page_num"] == ["1"]
    assert query["page_size"] == ["10"]
    assert [item.id for item in result.items] == ["baidu-0", "baidu-1", "baidu-2"]
    assert result.total == 24
    assert result.page == 2
    assert result.page_size == 3


def test_nearby_search_uses_around_endpoint_and_gcj02_coordinates() -> None:
    transport = RecordingTransport([json_response(search_payload([] , total=0))])
    provider = provider_for(transport)

    result = provider.search(
        HotelNearbySearchRequest(
            latitude=24.4798,
            longitude=118.0894,
            radius=3000,
            keyword="海景酒店",
            page=2,
            page_size=10,
        )
    )

    request = transport.requests[0]
    assert urlparse(str(request.url)).path == "/place/v3/around"
    query = parse_qs(urlparse(str(request.url)).query)
    assert query == {
        "query": ["海景酒店"],
        "location": ["24.4798,118.0894"],
        "radius": ["3000"],
        "radius_limit": ["true"],
        "scope": ["2"],
        "coord_type": ["2"],
        "page_num": ["1"],
        "page_size": ["10"],
        "filter": ["industry_type:hotel"],
        "ret_coordtype": ["gcj02ll"],
        "output": ["json"],
        "ak": [REAL_TEST_AK],
    }
    assert result.status == "success"
    assert result.items == []
    assert result.total == 0


def test_invalid_optional_fields_do_not_discard_valid_hotel() -> None:
    payload = search_payload(
        [
            hotel_result(
                uid="valid-with-gaps",
                detail_info={"overall_rating": "not-a-rating", "distance": "unknown"},
                address=None,
                telephone=None,
            ),
            hotel_result(uid="", name="should be skipped"),
            hotel_result(uid="missing-name", name=""),
        ],
        total=3,
    )
    transport = RecordingTransport([json_response(payload)])
    provider = provider_for(transport)

    result = provider.search(HotelSearchRequest(city="厦门"))

    assert len(result.items) == 1
    item = result.items[0]
    assert item.id == "valid-with-gaps"
    assert item.address is None
    assert item.telephone is None
    assert item.rating is None
    assert item.distance is None


def test_empty_search_results_are_a_successful_empty_domain_result() -> None:
    transport = RecordingTransport([json_response(search_payload([], total=0))])
    provider = provider_for(transport)

    result = provider.search(HotelSearchRequest(city="厦门"))

    assert result.status == "success"
    assert result.items == []
    assert result.total == 0
    assert result.warning is None


def test_detail_search_uses_uid_and_maps_detail_fields() -> None:
    payload = {
        "status": 0,
        "message": "ok",
        "results": [
            {
                "uid": "baidu-1",
                "name": "厦门海景酒店",
                "address": "环岛路 1 号",
                "location": {"lat": 24.44, "lng": 118.08},
                "telephone": "0592-1234567",
                "detail_info": {
                    "overall_rating": 4.9,
                    "tag": "酒店,亲子",
                    "classified_poi_tag": "高档型",
                    "shop_hours": "全天",
                    "description": "靠近海边",
                    "detail_url": "https://map.baidu.com/detail/baidu-1",
                },
            },
        ],
    }
    transport = RecordingTransport([json_response(payload)])
    provider = provider_for(transport)

    detail = provider.get_detail("baidu-1")

    request = transport.requests[0]
    assert urlparse(str(request.url)).path == "/place/v3/detail"
    assert parse_qs(urlparse(str(request.url)).query) == {
        "uid": ["baidu-1"],
        "scope": ["2"],
        "ret_coordtype": ["gcj02ll"],
        "output": ["json"],
        "ak": [REAL_TEST_AK],
    }
    assert detail is not None
    assert detail.id == "baidu-1"
    assert detail.rating == 4.9
    assert detail.tags == ["酒店", "亲子", "高档型"]
    assert detail.business_hours == "全天"
    assert detail.description == "靠近海边"
    assert detail.detail_url == "https://map.baidu.com/detail/baidu-1"


def test_detail_search_maps_live_list_response_shape() -> None:
    payload = {
        "status": 0,
        "message": "ok",
        "results": [
            {
                "uid": "test-live-shape-uid",
                "name": "测试地点",
                "address": "测试地址",
                "location": {"lat": 39.9, "lng": 116.4},
                "telephone": "010-test",
                "detail_info": {
                    "overall_rating": "4.5",
                    "tag": "酒店",
                    "shop_hours": "全天",
                    "detail_url": "https://example.invalid/detail",
                },
            }
        ],
    }
    transport = RecordingTransport([json_response(payload)])
    provider = provider_for(transport)

    detail = provider.get_detail("test-live-shape-uid")

    assert isinstance(detail, HotelDetail)
    assert detail.id == "test-live-shape-uid"
    assert detail.name == "测试地点"
    assert detail.address == "测试地址"
    assert detail.latitude == 39.9
    assert detail.longitude == 116.4
    assert detail.telephone == "010-test"
    assert detail.rating == 4.5
    assert detail.provider == "baidu"
    assert detail.tags == ["酒店"]
    assert detail.business_hours == "全天"
    assert detail.detail_url == "https://example.invalid/detail"


@pytest.mark.parametrize("results", ["wrong", [123], [{}]])
def test_detail_invalid_results_shape_raises_sanitized_error(results: object) -> None:
    transport = RecordingTransport(
        [json_response({"status": 0, "message": "ok", "results": results})]
    )
    provider = provider_for(transport)

    with pytest.raises(BaiduHotelProviderError) as error:
        provider.get_detail("test-live-shape-uid")

    assert error.value.code == "BAIDU_HOTEL_INVALID_RESPONSE"


def test_detail_without_a_poi_returns_none() -> None:
    transport = RecordingTransport(
        [json_response({"status": 0, "message": "ok", "results": []})]
    )
    provider = provider_for(transport)

    assert provider.get_detail("missing-uid") is None


@pytest.mark.parametrize(
    ("response", "expected_code"),
    [
        (json_response({"status": 1, "message": "bad ak"}), "BAIDU_HOTEL_PROVIDER_ERROR"),
        (json_response({}, status_code=503), "BAIDU_HOTEL_HTTP_ERROR"),
        (httpx.ReadTimeout("timed out"), "BAIDU_HOTEL_TIMEOUT"),
        (httpx.ConnectError("network down"), "BAIDU_HOTEL_NETWORK_ERROR"),
    ],
)
def test_provider_failures_raise_stable_errors_without_upstream_text(
    response: httpx.Response | Exception,
    expected_code: str,
) -> None:
    transport = RecordingTransport([response])
    provider = provider_for(transport)

    with pytest.raises(BaiduHotelProviderError) as error:
        provider.search(HotelSearchRequest(city="厦门"))

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
def test_invalid_upstream_payload_raises_sanitized_error(
    response: httpx.Response,
) -> None:
    transport = RecordingTransport([response])
    provider = provider_for(transport)

    with pytest.raises(BaiduHotelProviderError) as error:
        provider.search(HotelSearchRequest(city="厦门"))

    assert error.value.code == "BAIDU_HOTEL_INVALID_RESPONSE"
    assert REAL_TEST_AK not in str(error.value)
