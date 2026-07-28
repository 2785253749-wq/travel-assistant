from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from app.api.auth import AuthenticatedUser, get_supabase_auth_gateway_factory
from app.infrastructure.repositories import InMemoryTripRepository
from app.main import app
from app.trips.service import TripService, get_public_trip_service, get_trip_service


USER_A = UUID("11111111-1111-1111-1111-111111111111")
USER_B = UUID("22222222-2222-2222-2222-222222222222")


class FakeAuthGateway:
    def get_user(self, token: str) -> AuthenticatedUser:
        if token == "user-a":
            return AuthenticatedUser(id=USER_A, email="a@example.com")
        if token == "user-b":
            return AuthenticatedUser(id=USER_B, email="b@example.com")
        raise RuntimeError("not used in this test")


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    from app.core.config import get_settings

    get_settings.cache_clear()
    app.dependency_overrides[get_supabase_auth_gateway_factory] = lambda: lambda: FakeAuthGateway()
    service = TripService(InMemoryTripRepository())
    app.dependency_overrides[get_trip_service] = lambda: service
    app.dependency_overrides[get_public_trip_service] = lambda: service
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
    get_settings.cache_clear()


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _create_trip(client: TestClient) -> dict:
    response = client.post(
        "/api/trips",
        headers=_headers("user-a"),
        json={"profile": {"origin": "Shanghai", "destination": "Hangzhou"}},
    )
    assert response.status_code == 201
    return response.json()


def test_private_crud_uses_verified_owner_and_ignores_body_user_id(client):
    trip = _create_trip(client)
    assert trip["user_id"] == str(USER_A)

    response = client.patch(
        f"/api/trips/{trip['id']}",
        headers=_headers("user-a"),
        json={"title": "updated", "user_id": str(USER_B)},
    )
    assert response.status_code == 200
    assert response.json()["user_id"] == str(USER_A)

    forbidden = client.get(f"/api/trips/{trip['id']}", headers=_headers("user-b"))
    assert forbidden.status_code == 404
    assert forbidden.json()["detail"]["code"] == "TRIP_NOT_FOUND"


def test_share_endpoint_is_public_read_only_and_revocable(client):
    trip = _create_trip(client)
    share = client.post(f"/api/trips/{trip['id']}/share", headers=_headers("user-a"))
    assert share.status_code == 201
    token = share.json()["token"]

    public = client.get(f"/api/shared/{token}")
    assert public.status_code == 200
    assert set(public.json()) == {"id", "title", "status", "profile", "itinerary", "updated_at"}
    assert "user_id" not in public.text

    revoked = client.delete(f"/api/trips/{trip['id']}/share", headers=_headers("user-a"))
    assert revoked.status_code == 204
    assert client.get(f"/api/shared/{token}").status_code == 404


def test_non_owner_cannot_list_mutate_or_manage_share_links(client):
    trip = _create_trip(client)

    assert client.get("/api/trips", headers=_headers("user-b")).json() == []
    for method, suffix in (("patch", ""), ("delete", ""), ("post", "/share"), ("delete", "/share")):
        response = client.request(
            method.upper(), f"/api/trips/{trip['id']}{suffix}", headers=_headers("user-b"), json={"title": "stolen"}
        )
        assert response.status_code == 404
        assert response.json()["detail"]["code"] == "TRIP_NOT_FOUND"


def test_development_default_services_share_one_in_memory_store(monkeypatch):
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_ANON_KEY", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_KEY", raising=False)
    from app.core.config import get_settings
    from app.trips import service as service_module

    get_settings.cache_clear()
    for name in ("get_trip_service", "get_public_trip_service", "get_development_repository"):
        dependency = getattr(service_module, name, None)
        if dependency is not None and hasattr(dependency, "cache_clear"):
            dependency.cache_clear()
    app.dependency_overrides.clear()
    app.dependency_overrides[get_supabase_auth_gateway_factory] = (
        lambda: lambda: FakeAuthGateway()
    )

    with TestClient(app) as development_client:
        trip = _create_trip(development_client)
        share = development_client.post(
            f"/api/trips/{trip['id']}/share", headers=_headers("user-a")
        )
        assert share.status_code == 201

        public = development_client.get(f"/api/shared/{share.json()['token']}")

    assert public.status_code == 200
    assert public.json()["id"] == trip["id"]
    app.dependency_overrides.clear()
    get_settings.cache_clear()
    for name in ("get_trip_service", "get_public_trip_service", "get_development_repository"):
        dependency = getattr(service_module, name, None)
        if dependency is not None and hasattr(dependency, "cache_clear"):
            dependency.cache_clear()
