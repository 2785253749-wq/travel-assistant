from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from app.api.auth import AuthenticatedUser, get_supabase_auth_gateway_factory
from app.core.errors import AppError
from app.main import app


USER_A = UUID("11111111-1111-1111-1111-111111111111")
USER_B = UUID("22222222-2222-2222-2222-222222222222")


class FakeAuthGateway:
    def get_user(self, token: str) -> AuthenticatedUser:
        if token == "user-a":
            return AuthenticatedUser(
                id=USER_A, email="alice@example.com", access_token=token
            )
        if token == "user-b":
            return AuthenticatedUser(
                id=USER_B, email="bob@example.com", access_token=token
            )
        raise RuntimeError("unexpected test token")


class RaisingModule:
    def __init__(self, code: str) -> None:
        self._code = code

    def list(self, _user_id: UUID):
        raise AppError(self._code, "private implementation detail")


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    from app import composition as composition_module
    from app.core.config import get_settings

    get_settings.cache_clear()
    development_module = getattr(
        composition_module, "get_development_footprint_module", None
    )
    if development_module is not None:
        development_module.cache_clear()
    app.dependency_overrides[get_supabase_auth_gateway_factory] = (
        lambda: lambda: FakeAuthGateway()
    )
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
    if development_module is not None:
        development_module.cache_clear()
    get_settings.cache_clear()


def _headers(token: str = "user-a") -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _create(client: TestClient) -> dict:
    response = client.post(
        "/api/footprints",
        headers=_headers(),
        json={"city_adcode": "350200", "visited_at": "2025-01-02"},
    )
    assert response.status_code == 201
    return response.json()


def test_list_requires_bearer(client):
    response = client.get("/api/footprints")

    assert response.status_code == 401
    assert response.json()["detail"] == {
        "code": "AUTH_REQUIRED",
        "message": "Bearer token required",
    }


def test_city_search_requires_auth_and_valid_query(client):
    assert client.get("/api/map/cities?q=厦门").status_code == 401
    assert client.get("/api/map/cities?q=x", headers=_headers()).status_code == 422
    assert client.get("/api/map/cities?q=%20%20", headers=_headers()).status_code == 422
    assert client.get("/api/map/cities?q=%20x%20", headers=_headers()).status_code == 422


def test_city_search_returns_normalized_trial_candidates(client):
    from app.composition import get_district_boundary_service
    from app.footprints.districts import UnavailableDistrictBoundaryService

    app.dependency_overrides[get_district_boundary_service] = (
        lambda: UnavailableDistrictBoundaryService()
    )

    response = client.get("/api/map/cities?q=厦门", headers=_headers())

    assert response.status_code == 200
    assert response.json() == [
        {
            "city_adcode": "350200",
            "city_name": "厦门市",
            "province_adcode": "350000",
            "province_name": "福建省",
            "center": [118.09, 24.48],
        }
    ]


def test_boundary_unavailable_never_leaks_server_secret(client):
    from app.composition import get_district_boundary_service
    from app.footprints.districts import UnavailableDistrictBoundaryService

    app.dependency_overrides[get_district_boundary_service] = (
        lambda: UnavailableDistrictBoundaryService()
    )

    response = client.get("/api/map/districts/350200", headers=_headers())

    assert response.status_code == 200
    assert response.json()["status"] == "unavailable"
    assert response.json()["city"]["center"] == [118.09, 24.48]
    assert "server-secret" not in response.text
    assert "restapi.amap.com" not in response.text


def test_boundary_requires_auth_and_a_six_digit_adcode(client):
    assert client.get("/api/map/districts/350200").status_code == 401
    assert client.get("/api/map/districts/not-an-adcode", headers=_headers()).status_code == 422
    assert client.get("/api/map/districts/１２３４５６", headers=_headers()).status_code == 422


def test_crud_uses_verified_owner_and_omits_account_identity(client):
    created = _create(client)

    assert set(created) == {
        "id",
        "city_adcode",
        "city_name",
        "province_adcode",
        "province_name",
        "center",
        "visited_at",
        "created_at",
        "updated_at",
    }
    assert "user_id" not in str(created)
    assert "alice@example.com" not in str(created)

    listed = client.get("/api/footprints", headers=_headers())
    assert listed.status_code == 200
    assert listed.json() == [created]
    assert "user_id" not in listed.text
    assert "email" not in listed.text

    other_user_list = client.get("/api/footprints", headers=_headers("user-b"))
    assert other_user_list.status_code == 200
    assert other_user_list.json() == []

    other_user_update = client.patch(
        f"/api/footprints/{created['id']}",
        headers=_headers("user-b"),
        json={"visited_at": "2025-01-03"},
    )
    assert other_user_update.status_code == 404
    assert other_user_update.json()["detail"]["code"] == "FOOTPRINT_NOT_FOUND"

    updated = client.patch(
        f"/api/footprints/{created['id']}",
        headers=_headers(),
        json={"visited_at": "2025-01-03"},
    )
    assert updated.status_code == 200
    assert updated.json()["visited_at"] == "2025-01-03"

    other_user_delete = client.delete(
        f"/api/footprints/{created['id']}", headers=_headers("user-b")
    )
    assert other_user_delete.status_code == 404
    assert other_user_delete.json()["detail"]["code"] == "FOOTPRINT_NOT_FOUND"

    deleted = client.delete(f"/api/footprints/{created['id']}", headers=_headers())
    assert deleted.status_code == 204
    assert deleted.content == b""
    assert client.get("/api/footprints", headers=_headers()).json() == []


@pytest.mark.parametrize(
    ("code", "expected_status", "expected_message"),
    [
        ("FOOTPRINT_VALIDATION_FAILED", 422, "Footprint request validation failed"),
        ("FOOTPRINT_CITY_NOT_FOUND", 404, "Footprint city not found"),
        ("FOOTPRINT_NOT_FOUND", 404, "Footprint not found"),
        ("FOOTPRINT_UNAVAILABLE", 503, "Footprint service unavailable"),
    ],
)
def test_api_maps_domain_errors_to_stable_public_responses(
    client, code, expected_status, expected_message
):
    from app.composition import get_footprint_module

    app.dependency_overrides[get_footprint_module] = lambda: RaisingModule(code)

    response = client.get("/api/footprints", headers=_headers())

    assert response.status_code == expected_status
    assert response.json()["detail"] == {
        "code": code,
        "message": expected_message,
    }
    assert "private implementation detail" not in response.text
