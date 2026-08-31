from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from app.locations.models import LocationQuery
from app.providers.baidu_location import (
    BaiduLocationProvider,
    BaiduLocationProviderError,
)
from tests.fixtures.providers import RecordingTransport, json_response


REAL_TEST_AK = "fake-baidu-ak-secret"


def provider_for(
    transport: RecordingTransport,
    *,
    api_key: str = REAL_TEST_AK,
    timeout: float = 2.5,
) -> BaiduLocationProvider:
    return BaiduLocationProvider(
        api_key=api_key,
        client=httpx.Client(transport=transport),
        timeout=timeout,
    )


def test_search_sends_expected_region_request() -> None:
    transport = RecordingTransport(
        [json_response({"status": 0, "message": "ok", "results": []})]
    )
    provider = provider_for(transport)

    provider.search(LocationQuery(query="厦门大学", city="厦门"))

    request = transport.requests[0]
    assert urlparse(str(request.url)).path == "/place/v3/region"
    assert parse_qs(urlparse(str(request.url)).query) == {
        "query": ["厦门大学"],
        "region": ["厦门"],
        "region_limit": ["true"],
        "scope": ["2"],
        "page_num": ["0"],
        "page_size": ["10"],
        "ret_coordtype": ["gcj02ll"],
        "output": ["json"],
        "ak": [REAL_TEST_AK],
    }
    assert request.extensions["timeout"]["read"] == 2.5


def test_search_maps_location_candidates() -> None:
    transport = RecordingTransport(
        [
            json_response(
                {
                    "status": 0,
                    "message": "ok",
                    "results": [
                        {
                            "uid": "poi-1",
                            "name": "厦门大学",
                            "location": {"lat": 24.438, "lng": 118.097},
                            "address": "测试地址",
                            "province": "福建省",
                            "city": "厦门市",
                            "area": "思明区",
                        }
                    ],
                }
            )
        ]
    )
    provider = provider_for(transport)

    result = provider.search(LocationQuery(query="厦门大学", city="厦门"))

    assert len(result.items) == 1
    item = result.items[0]
    assert item.id == "poi-1"
    assert item.name == "厦门大学"
    assert item.latitude == 24.438
    assert item.longitude == 118.097
    assert item.address == "测试地址"
    assert item.province == "福建省"
    assert item.city == "厦门市"
    assert item.district == "思明区"
    assert item.provider == "baidu"


def test_search_preserves_result_order() -> None:
    transport = RecordingTransport(
        [
            json_response(
                {
                    "status": 0,
                    "message": "ok",
                    "results": [
                        {
                            "uid": "poi-a",
                            "name": "地点 A",
                            "location": {"lat": 24.4, "lng": 118.1},
                        },
                        {
                            "uid": "poi-b",
                            "name": "地点 B",
                            "location": {"lat": 24.5, "lng": 118.2},
                        },
                    ],
                }
            )
        ]
    )
    provider = provider_for(transport)

    result = provider.search(LocationQuery(query="地点", city="厦门"))

    assert [item.id for item in result.items] == ["poi-a", "poi-b"]
    assert [item.name for item in result.items] == ["地点 A", "地点 B"]


def test_empty_results_are_a_successful_empty_location_result() -> None:
    transport = RecordingTransport(
        [json_response({"status": 0, "message": "ok", "results": []})]
    )
    provider = provider_for(transport)

    result = provider.search(LocationQuery(query="不存在的地点", city="厦门"))

    assert result.items == []
    assert result.provider == "baidu"


def test_city_is_required_without_sending_http() -> None:
    transport = RecordingTransport([])
    provider = provider_for(transport)

    with pytest.raises(BaiduLocationProviderError) as error:
        provider.search(LocationQuery(query="厦门大学"))

    assert error.value.code == "BAIDU_LOCATION_REGION_REQUIRED"
    assert transport.requests == []


def test_malformed_individual_pois_are_skipped() -> None:
    transport = RecordingTransport(
        [
            json_response(
                {
                    "status": 0,
                    "message": "ok",
                    "results": [
                        {"uid": "missing-name", "location": {"lat": 24.4, "lng": 118.1}},
                        {"uid": "missing-location", "name": "缺坐标"},
                        {
                            "uid": "invalid-latitude",
                            "name": "非法纬度",
                            "location": {"lat": 91, "lng": 118.1},
                        },
                        {
                            "uid": "invalid-longitude",
                            "name": "非法经度",
                            "location": {"lat": 24.4, "lng": 181},
                        },
                        {
                            "name": "无 UID 但坐标有效",
                            "location": {"lat": 24.4, "lng": 118.1},
                        },
                    ],
                }
            )
        ]
    )
    provider = provider_for(transport)

    result = provider.search(LocationQuery(query="地点", city="厦门"))

    assert len(result.items) == 1
    assert result.items[0].id is None
    assert result.items[0].name == "无 UID 但坐标有效"


@pytest.mark.parametrize(
    "payload",
    [
        {"status": 0, "message": "ok", "results": "wrong"},
        {"message": "ok", "results": []},
        {"status": 0, "message": "ok"},
        ["wrong"],
    ],
)
def test_invalid_top_level_results_shape_raises_sanitized_error(
    payload: object,
) -> None:
    transport = RecordingTransport([json_response(payload)])
    provider = provider_for(transport)

    with pytest.raises(BaiduLocationProviderError) as error:
        provider.search(LocationQuery(query="地点", city="厦门"))

    assert error.value.code == "BAIDU_LOCATION_INVALID_RESPONSE"
    assert REAL_TEST_AK not in str(error.value)


def test_baidu_status_error_is_sanitized() -> None:
    transport = RecordingTransport(
        [
            json_response(
                {
                    "status": 1,
                    "message": f"invalid ak {REAL_TEST_AK}",
                    "results": [],
                }
            )
        ]
    )
    provider = provider_for(transport)

    with pytest.raises(BaiduLocationProviderError) as error:
        provider.search(LocationQuery(query="地点", city="厦门"))

    assert error.value.code == "BAIDU_LOCATION_PROVIDER_ERROR"
    assert REAL_TEST_AK not in str(error.value)
    assert "invalid ak" not in str(error.value)


def test_missing_api_key_is_rejected_without_http() -> None:
    transport = RecordingTransport([])

    with pytest.raises(BaiduLocationProviderError) as error:
        provider_for(transport, api_key=" ")

    assert error.value.code == "BAIDU_LOCATION_NOT_CONFIGURED"
    assert transport.requests == []


@pytest.mark.parametrize(
    ("response", "expected_code"),
    [
        (httpx.ReadTimeout("timed out"), "BAIDU_LOCATION_TIMEOUT"),
        (httpx.ConnectError("network down"), "BAIDU_LOCATION_NETWORK_ERROR"),
        (
            httpx.Response(503, content=f"upstream {REAL_TEST_AK}".encode()),
            "BAIDU_LOCATION_HTTP_ERROR",
        ),
        (
            httpx.Response(200, content=f"<html>{REAL_TEST_AK}</html>".encode()),
            "BAIDU_LOCATION_INVALID_RESPONSE",
        ),
    ],
)
def test_request_failures_are_stable_and_sanitized(
    response: httpx.Response | Exception,
    expected_code: str,
) -> None:
    transport = RecordingTransport([response])
    provider = provider_for(transport)

    with pytest.raises(BaiduLocationProviderError) as error:
        provider.search(LocationQuery(query="地点", city="厦门"))

    assert error.value.code == expected_code
    assert REAL_TEST_AK not in str(error.value)
    assert "?ak=" not in str(error.value)
    assert "upstream" not in str(error.value)
    assert "network down" not in str(error.value)
