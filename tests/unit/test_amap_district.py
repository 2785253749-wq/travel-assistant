from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from app.core.config import Settings
from app.footprints.models import CityRecord
from app.providers.amap_district import AmapDistrictProvider
from tests.fixtures.providers import RecordingTransport, json_response


def district_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "amap_web_service_key": "server-secret",
        "amap_js_key": "browser-public-key",
        "district_timeout_seconds": 2.5,
        "_env_file": None,
    }
    values.update(overrides)
    return Settings(**values)


def city_district(**overrides: object) -> dict[str, object]:
    district: dict[str, object] = {
        "citycode": "0592",
        "adcode": "350200",
        "name": "厦门市",
        "center": "118.0894,24.4798",
        "level": "city",
        "province": "福建省",
        "districts": [],
        "polyline": "118.0,24.0;119.0,24.0;119.0,25.0",
    }
    district.update(overrides)
    return district


def success_payload(*districts: dict[str, object]) -> dict[str, object]:
    return {
        "status": "1",
        "count": str(len(districts)),
        "info": "OK",
        "infocode": "10000",
        "districts": list(districts),
    }


def provider_for(
    responses: list[httpx.Response | Exception], **settings_overrides: object
) -> tuple[AmapDistrictProvider, RecordingTransport]:
    transport = RecordingTransport(responses)
    return (
        AmapDistrictProvider(
            settings=district_settings(**settings_overrides),
            client=httpx.Client(transport=transport),
        ),
        transport,
    )


def test_boundary_uses_server_key_and_all_extensions() -> None:
    provider, transport = provider_for([json_response(success_payload(city_district()))])

    result = provider.boundary("350200")

    query = parse_qs(urlparse(str(transport.requests[0].url)).query)
    assert query == {
        "key": ["server-secret"],
        "keywords": ["350200"],
        "subdistrict": ["0"],
        "extensions": ["all"],
        "output": ["JSON"],
    }
    assert transport.requests[0].extensions["timeout"]["read"] == 2.5
    assert result.degraded is False


def test_boundary_parses_a_closed_finite_numeric_ring() -> None:
    provider, _ = provider_for([json_response(success_payload(city_district()))])

    result = provider.boundary("350200")

    assert result.data is not None
    assert result.data.city.city_adcode == "350200"
    assert result.data.city.province_adcode == "350000"
    assert result.data.rings == [
        [(118.0, 24.0), (119.0, 24.0), (119.0, 25.0), (118.0, 24.0)]
    ]


def test_search_returns_canonical_city_records_and_normalizes_direct_municipalities() -> None:
    direct_municipality = city_district(
        adcode="110000",
        name="北京市",
        center="116.4074,39.9042",
        level="province",
        province="北京市",
    )
    ordinary_province = city_district(
        adcode="350000", name="福建省", level="province", province="福建省"
    )
    provider, transport = provider_for(
        [json_response(success_payload(city_district(), direct_municipality, ordinary_province))]
    )

    cities = provider.search("厦门")

    query = parse_qs(urlparse(str(transport.requests[0].url)).query)
    assert query == {
        "key": ["server-secret"],
        "keywords": ["厦门"],
        "subdistrict": ["0"],
        "extensions": ["base"],
        "output": ["JSON"],
    }
    assert cities == [
        CityRecord(
            city_adcode="350200",
            city_name="厦门市",
            province_adcode="350000",
            province_name="福建省",
            center=(118.0894, 24.4798),
        ),
        CityRecord(
            city_adcode="110000",
            city_name="北京市",
            province_adcode="110000",
            province_name="北京市",
            center=(116.4074, 39.9042),
        ),
    ]


def test_search_missing_server_key_does_not_fall_back_to_browser_key_or_http() -> None:
    provider, transport = provider_for([], amap_web_service_key=None)

    assert provider.search("厦门") == []
    assert transport.requests == []


@pytest.mark.parametrize(
    "response",
    [
        httpx.ConnectError("offline"),
        json_response({}, status_code=503),
        json_response({"status": "0", "info": "failure", "infocode": "10001"}),
    ],
)
def test_search_upstream_failures_return_an_empty_safe_catalog(
    response: httpx.Response | Exception,
) -> None:
    provider, _ = provider_for([response])

    assert provider.search("厦门") == []


def test_search_redirect_response_with_valid_looking_payload_returns_an_empty_catalog() -> None:
    provider, _ = provider_for(
        [json_response(success_payload(city_district()), status_code=302)]
    )

    assert provider.search("厦门") == []


@pytest.mark.parametrize(
    "payload",
    [
        success_payload(),
        success_payload(city_district(center="not-a-center")),
        success_payload(city_district(polyline="118.0,24.0;bad,24.0;119.0,25.0")),
        success_payload(city_district(polyline="118.0,24.0;119.0,24.0;181.0,25.0")),
    ],
)
def test_malformed_district_payload_is_an_unavailable_boundary(payload: dict[str, object]) -> None:
    provider, _ = provider_for([json_response(payload)])

    result = provider.boundary("350200")

    assert result.data is None
    assert result.degraded is True
    assert result.error_code == "DISTRICT_INVALID_RESPONSE"


def test_boundary_rejects_more_than_the_configured_point_limit() -> None:
    polyline = "118.0,24.0;119.0,24.0;119.0,25.0;118.0,25.0"
    provider, _ = provider_for(
        [json_response(success_payload(city_district(polyline=polyline)))],
        district_max_points=4,
    )

    result = provider.boundary("350200")

    assert result.data is None
    assert result.degraded is True
    assert result.error_code == "DISTRICT_INVALID_RESPONSE"


def test_boundary_redirect_response_with_valid_looking_payload_is_unavailable() -> None:
    provider, _ = provider_for(
        [json_response(success_payload(city_district()), status_code=302)]
    )

    result = provider.boundary("350200")

    assert result.data is None
    assert result.degraded is True
    assert result.error_code == "DISTRICT_HTTP_ERROR"


def test_http_error_is_unavailable_without_exposing_upstream_text_or_server_key() -> None:
    provider, _ = provider_for(
        [json_response({"info": "server-secret raw upstream response"}, status_code=401)]
    )

    result = provider.boundary("350200")

    assert result.data is None
    assert result.degraded is True
    assert result.error_code == "DISTRICT_HTTP_ERROR"
    assert "server-secret" not in result.error_code
    assert "upstream" not in result.error_code
    assert "restapi.amap.com" not in result.error_code


def test_amap_non_success_response_is_an_unavailable_boundary() -> None:
    provider, _ = provider_for(
        [json_response({"status": "0", "info": "INVALID_USER_KEY", "infocode": "10001"})]
    )

    result = provider.boundary("350200")

    assert result.data is None
    assert result.degraded is True
    assert result.error_code == "DISTRICT_PROVIDER_ERROR"
