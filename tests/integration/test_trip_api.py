from uuid import UUID
from pathlib import Path
import logging

import pytest
from fastapi.testclient import TestClient

from app.api.auth import AuthenticatedUser, get_supabase_auth_gateway_factory
from app.composition import get_public_trip_service, get_trip_service
from app.infrastructure.repositories import InMemoryTripRepository
from app.main import app
from app.core.logging import database_operation
from app.schemas import Itinerary
from app.trips.service import TripService


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
def trip_service():
    return TripService(InMemoryTripRepository())


@pytest.fixture
def client(monkeypatch, trip_service):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    from app.core.config import get_settings

    get_settings.cache_clear()
    app.dependency_overrides[get_supabase_auth_gateway_factory] = lambda: lambda: FakeAuthGateway()
    app.dependency_overrides[get_trip_service] = lambda: trip_service
    app.dependency_overrides[get_public_trip_service] = lambda: trip_service
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


def test_public_share_resolve_binds_a_token_digest_to_database_logs(client, caplog):
    token = "private-share-token"

    class LoggingPublicService:
        def get_shared_trip(self, presented_token):
            assert presented_token == token
            with database_operation(
                "share.resolve.synthetic", subject="share-digest:synthetic"
            ):
                return {"status": "planned"}

    app.dependency_overrides[get_public_trip_service] = lambda: LoggingPublicService()
    with caplog.at_level(logging.INFO, logger="app.database"):
        response = client.post("/api/shared/resolve", json={"token": token})

    assert response.status_code == 200
    record = next(record for record in caplog.records if record.message == "database_result")
    assert record.subject.startswith("share-digest:")
    assert token not in caplog.text


def test_private_crud_uses_verified_owner(client):
    trip = _create_trip(client)
    assert trip["user_id"] == str(USER_A)

    response = client.patch(
        f"/api/trips/{trip['id']}",
        headers=_headers("user-a"),
        json={"title": "updated"},
    )
    assert response.status_code == 200
    assert response.json()["user_id"] == str(USER_A)

    forbidden = client.get(f"/api/trips/{trip['id']}", headers=_headers("user-b"))
    assert forbidden.status_code == 404
    assert forbidden.json()["detail"]["code"] == "TRIP_NOT_FOUND"


def test_client_cannot_write_planned_status_itinerary_or_unknown_fields(client):
    trip = _create_trip(client)
    forged = {"title": "forged", "days": [{"date": "2099-01-01"}]}

    for payload in (
        {"itinerary": forged},
        {"status": "planned"},
        {"profile": {"destination": "Forged"}},
        {"title": "updated", "user_id": str(USER_B)},
    ):
        response = client.patch(
            f"/api/trips/{trip['id']}", headers=_headers("user-a"), json=payload
        )
        assert response.status_code == 422
        assert response.json()["detail"]["code"] == "REQUEST_INVALID"

    unchanged = client.get(f"/api/trips/{trip['id']}", headers=_headers("user-a"))
    assert unchanged.json()["status"] == "collecting"
    assert unchanged.json()["itinerary"] is None


def test_copy_endpoint_clones_only_server_validated_trip(client, trip_service):
    trip = _create_trip(client)
    itinerary = Itinerary.model_validate_json(
        Path("tests/fixtures/task7_itinerary.json").read_text(encoding="utf-8")
    )
    trip_service.update_trip(
        USER_A, UUID(trip["id"]), status="planned", itinerary=itinerary
    )

    response = client.post(
        f"/api/trips/{trip['id']}/copy", headers=_headers("user-a")
    )

    assert response.status_code == 201
    copied = response.json()
    assert copied["id"] != trip["id"]
    assert copied["user_id"] == str(USER_A)
    assert copied["status"] == "planned"
    assert copied["itinerary"] == itinerary.model_dump(mode="json")


def test_share_endpoint_is_public_read_only_and_revocable(client):
    trip = _create_trip(client)
    share = client.post(f"/api/trips/{trip['id']}/share", headers=_headers("user-a"))
    assert share.status_code == 201
    token = share.json()["token"]

    public = client.post("/api/shared/resolve", json={"token": token})
    assert public.status_code == 200
    assert set(public.json()) == {"id", "title", "status", "profile", "itinerary", "updated_at"}
    assert "user_id" not in public.text

    revoked = client.delete(f"/api/trips/{trip['id']}/share", headers=_headers("user-a"))
    assert revoked.status_code == 204
    assert client.post("/api/shared/resolve", json={"token": token}).status_code == 404


def test_non_owner_cannot_list_mutate_or_manage_share_links(client):
    trip = _create_trip(client)

    assert client.get("/api/trips", headers=_headers("user-b")).json() == []
    for method, suffix in (("patch", ""), ("delete", ""), ("post", "/copy"), ("post", "/share"), ("delete", "/share")):
        payload = {"title": "stolen"} if method == "patch" else None
        response = client.request(
            method.upper(),
            f"/api/trips/{trip['id']}{suffix}",
            headers=_headers("user-b"),
            json=payload,
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
    from app import composition as service_module

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

    public = development_client.post(
        "/api/shared/resolve", json={"token": share.json()["token"]}
    )

    assert public.status_code == 200
    assert public.json()["id"] == trip["id"]
    app.dependency_overrides.clear()
    get_settings.cache_clear()
    for name in ("get_trip_service", "get_public_trip_service", "get_development_repository"):
        dependency = getattr(service_module, name, None)
        if dependency is not None and hasattr(dependency, "cache_clear"):
            dependency.cache_clear()
