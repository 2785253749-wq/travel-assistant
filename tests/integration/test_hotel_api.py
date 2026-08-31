from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app.hotels.models import (
    HotelDetail,
    HotelNearbySearchRequest,
    HotelSearchRequest,
    HotelSearchResult,
    HotelSummary,
)
from app.main import app
from app.providers.baidu_hotel import BaiduHotelProviderError


def _result(*items: HotelSummary, page: int = 1, page_size: int = 10) -> HotelSearchResult:
    return HotelSearchResult(
        items=list(items),
        total=len(items),
        page=page,
        page_size=page_size,
        provider="fake",
        status="success",
        fetched_at=datetime.now(timezone.utc),
    )


def _summary(hotel_id: str = "hotel-1", name: str = "厦门测试酒店") -> HotelSummary:
    return HotelSummary(
        id=hotel_id,
        name=name,
        address="思明区测试路 1 号",
        latitude=24.4798,
        longitude=118.0894,
        rating=4.5,
        telephone="0592-1234567",
        distance=300,
        provider="fake",
    )


class FakeHotelService:
    def __init__(self) -> None:
        self.city_requests: list[HotelSearchRequest] = []
        self.nearby_requests: list[HotelNearbySearchRequest] = []
        self.detail_ids: list[str] = []
        self.city_result = _result(_summary())
        self.nearby_result = _result(_summary())
        self.detail_result: HotelDetail | None = HotelDetail(
            **_summary().model_dump(),
            tags=["亲子", "近海"],
            business_hours="全天",
            description="测试酒店详情",
            detail_url="https://example.test/hotels/hotel-1",
        )
        self.city_error: BaiduHotelProviderError | None = None
        self.nearby_error: BaiduHotelProviderError | None = None
        self.detail_error: BaiduHotelProviderError | None = None

    def search_city(self, request: HotelSearchRequest) -> HotelSearchResult:
        self.city_requests.append(request)
        if self.city_error is not None:
            raise self.city_error
        return self.city_result

    def search_nearby(self, request: HotelNearbySearchRequest) -> HotelSearchResult:
        self.nearby_requests.append(request)
        if self.nearby_error is not None:
            raise self.nearby_error
        return self.nearby_result

    def get_detail(self, hotel_id: str) -> HotelDetail | None:
        if not hotel_id.strip():
            raise ValueError("hotel_id must be a non-empty string")
        self.detail_ids.append(hotel_id)
        if self.detail_error is not None:
            raise self.detail_error
        return self.detail_result


@pytest.fixture
def fake_service() -> FakeHotelService:
    from app.api.hotels import get_hotel_service_dependency

    service = FakeHotelService()
    app.dependency_overrides[get_hotel_service_dependency] = lambda: service
    yield service
    app.dependency_overrides.clear()


@pytest.fixture
def client() -> TestClient:
    with TestClient(app) as test_client:
        yield test_client


def test_search_city_returns_domain_result_and_binds_defaults(
    client: TestClient, fake_service: FakeHotelService
) -> None:
    response = client.get("/api/hotels/search", params={"city": "  厦门  "})

    assert response.status_code == 200
    assert response.json() == fake_service.city_result.model_dump(mode="json")
    assert fake_service.city_requests == [
        HotelSearchRequest(city="厦门", keyword="酒店", page=1, page_size=10)
    ]


def test_search_city_binds_explicit_pagination_and_keyword(
    client: TestClient, fake_service: FakeHotelService
) -> None:
    response = client.get(
        "/api/hotels/search",
        params={"city": "厦门", "keyword": "  民宿  ", "page": 2, "page_size": 3},
    )

    assert response.status_code == 200
    assert fake_service.city_requests == [
        HotelSearchRequest(city="厦门", keyword="民宿", page=2, page_size=3)
    ]


@pytest.mark.parametrize(
    "query",
    [
        {},
        {"city": "   "},
        {"city": "厦门", "page": "0"},
        {"city": "厦门", "page_size": "21"},
    ],
)
def test_search_city_rejects_invalid_query(
    client: TestClient, fake_service: FakeHotelService, query: dict[str, str]
) -> None:
    response = client.get("/api/hotels/search", params=query)

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "REQUEST_INVALID"
    assert fake_service.city_requests == []


def test_nearby_search_returns_domain_result_and_binds_query(
    client: TestClient, fake_service: FakeHotelService
) -> None:
    response = client.get(
        "/api/hotels/nearby",
        params={
            "latitude": "24.4798",
            "longitude": "118.0894",
            "radius": "1500",
            "keyword": "  酒店式公寓 ",
            "page": "2",
            "page_size": "5",
        },
    )

    assert response.status_code == 200
    assert response.json() == fake_service.nearby_result.model_dump(mode="json")
    assert fake_service.nearby_requests == [
        HotelNearbySearchRequest(
            latitude=24.4798,
            longitude=118.0894,
            radius=1500,
            keyword="酒店式公寓",
            page=2,
            page_size=5,
        )
    ]


@pytest.mark.parametrize(
    "query",
    [
        {"latitude": "91", "longitude": "118"},
        {"latitude": "24", "longitude": "181"},
        {"latitude": "24", "longitude": "118", "radius": "0"},
        {"latitude": "24", "longitude": "118", "radius": "20001"},
    ],
)
def test_nearby_search_rejects_invalid_query(
    client: TestClient, fake_service: FakeHotelService, query: dict[str, str]
) -> None:
    response = client.get("/api/hotels/nearby", params=query)

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "REQUEST_INVALID"
    assert fake_service.nearby_requests == []


def test_empty_search_result_is_a_successful_response(
    client: TestClient, fake_service: FakeHotelService
) -> None:
    fake_service.city_result = _result()

    response = client.get("/api/hotels/search", params={"city": "厦门"})

    assert response.status_code == 200
    assert response.json()["items"] == []


def test_detail_returns_hotel(client: TestClient, fake_service: FakeHotelService) -> None:
    response = client.get("/api/hotels/hotel-1")

    assert response.status_code == 200
    assert response.json() == fake_service.detail_result.model_dump(mode="json")
    assert fake_service.detail_ids == ["hotel-1"]


def test_detail_returns_404_when_hotel_is_missing(
    client: TestClient, fake_service: FakeHotelService
) -> None:
    fake_service.detail_result = None

    response = client.get("/api/hotels/missing-hotel")

    assert response.status_code == 404
    assert response.json()["detail"] == {
        "code": "HOTEL_NOT_FOUND",
        "message": "Hotel not found",
    }


def test_detail_rejects_blank_hotel_id(client: TestClient, fake_service: FakeHotelService) -> None:
    response = client.get("/api/hotels/%20%20")

    assert response.status_code == 422
    assert response.json()["detail"] == {
        "code": "REQUEST_INVALID",
        "message": "Request validation failed",
    }
    assert fake_service.detail_ids == []


@pytest.mark.parametrize("path", ["/api/hotels/search?city=厦门", "/api/hotels/nearby?latitude=24&longitude=118"])
def test_provider_failure_is_mapped_to_safe_503(
    client: TestClient, fake_service: FakeHotelService, path: str
) -> None:
    error = BaiduHotelProviderError(
        "BAIDU_HOTEL_TIMEOUT",
        "secret-key=do-not-leak https://api.map.baidu.com/private",
    )
    if path.startswith("/api/hotels/search"):
        fake_service.city_error = error
    else:
        fake_service.nearby_error = error

    response = client.get(path)

    assert response.status_code == 503
    assert response.json()["detail"] == {
        "code": "HOTEL_PROVIDER_UNAVAILABLE",
        "message": "Hotel service is temporarily unavailable",
    }
    assert "do-not-leak" not in response.text
    assert "api.map.baidu.com" not in response.text


def test_detail_provider_failure_is_mapped_to_safe_503(
    client: TestClient, fake_service: FakeHotelService
) -> None:
    fake_service.detail_error = BaiduHotelProviderError(
        "BAIDU_HOTEL_HTTP_ERROR", "upstream body contains baidu-ak=do-not-leak"
    )

    response = client.get("/api/hotels/hotel-1")

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "HOTEL_PROVIDER_UNAVAILABLE"
    assert "do-not-leak" not in response.text


def test_missing_baidu_key_is_mapped_to_503_without_leaking_configuration(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.composition import get_hotel_service
    from app.core.config import get_settings

    app.dependency_overrides.clear()
    monkeypatch.delenv("BAIDU_MAP_AK", raising=False)
    get_settings.cache_clear()
    get_hotel_service.cache_clear()

    response = client.get("/api/hotels/search", params={"city": "厦门"})

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "HOTEL_PROVIDER_UNAVAILABLE"
    assert "BAIDU_HOTEL_NOT_CONFIGURED" not in response.text
    assert "BAIDU_MAP_AK" not in response.text
