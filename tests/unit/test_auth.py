from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from app.api.auth import AuthenticatedUser, get_supabase_auth_gateway
from app.main import app


class FakeSupabaseAuthGateway:
    user_id = "11111111-1111-1111-1111-111111111111"

    def get_user(self, token: str) -> AuthenticatedUser:
        assert token == "valid"
        return AuthenticatedUser(id=UUID(self.user_id), email="traveler@example.com")


@pytest.fixture
def fake_supabase():
    return FakeSupabaseAuthGateway()


@pytest.fixture
def client(fake_supabase, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    from app.core.config import get_settings

    get_settings.cache_clear()
    app.dependency_overrides[get_supabase_auth_gateway] = lambda: fake_supabase
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
    get_settings.cache_clear()


def test_missing_bearer_token_is_401(client):
    """Removing the dependency from a protected route must be detected."""
    response = client.get("/api/trips")

    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "AUTH_REQUIRED"


def test_user_id_comes_from_verified_token(fake_supabase, client):
    """Using a request-supplied user id instead of verified identity must fail."""
    response = client.get(
        "/api/me?user_id=22222222-2222-2222-2222-222222222222",
        headers={"Authorization": "Bearer valid"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "id": fake_supabase.user_id,
        "email": "traveler@example.com",
    }


def test_invalid_verified_token_is_401(client):
    """Returning a user without successful token verification must be rejected."""
    response = client.get("/api/me", headers={"Authorization": "Bearer invalid"})

    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "AUTH_INVALID"
