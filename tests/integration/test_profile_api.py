from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from app.api.auth import AuthenticatedUser, get_supabase_auth_gateway_factory
from app.main import app
from app.profile.models import ProfileInput
from app.profile.repositories import SupabaseProfileRepository
from app.profile.service import InMemoryProfileRepository, ProfileModule


USER_A = UUID("11111111-1111-1111-1111-111111111111")


class FakeAuthGateway:
    def get_user(self, token: str) -> AuthenticatedUser:
        if token == "user-a":
            return AuthenticatedUser(id=USER_A, email="alice@example.com")
        raise RuntimeError("unexpected token")


class _FakeMediaGateway:
    def sign_paths(self, paths: list[str], expires_in: int | None = None) -> list[str]:
        del expires_in
        return [f"https://signed.example.test/{path}" for path in paths]


class _NoopCleanupQueue:
    def enqueue(self, paths: list[str], *, note_id=None, image_id=None) -> int:
        del paths, note_id, image_id
        return 0


@pytest.fixture
def profile_module() -> ProfileModule:
    return ProfileModule(
        InMemoryProfileRepository(),
        media_gateway=_FakeMediaGateway(),
        cleanup_queue=_NoopCleanupQueue(),
    )


@pytest.fixture
def client(monkeypatch, profile_module: ProfileModule):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    from app.composition import get_profile_module
    from app.core.config import get_settings

    get_settings.cache_clear()
    app.dependency_overrides[get_supabase_auth_gateway_factory] = (
        lambda: lambda: FakeAuthGateway()
    )
    app.dependency_overrides[get_profile_module] = lambda: profile_module
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
    get_settings.cache_clear()


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_profile_page_route_is_registered():
    paths = {route.path for route in app.routes if hasattr(route, "path")}

    assert "/profile" in paths


def test_get_profile_returns_default_shape_for_authenticated_user(client: TestClient):
    response = client.get("/api/profile", headers=_headers("user-a"))

    assert response.status_code == 200
    assert response.json() == {
        "user_id": str(USER_A),
        "email": "alice@example.com",
        "display_name": "",
        "bio": "",
        "home_city": "",
        "travel_styles": [],
        "avatar_url": None,
        "updated_at": None,
    }


def test_put_profile_replaces_profile_and_returns_stable_shape(
    client: TestClient, profile_module: ProfileModule
):
    response = client.put(
        "/api/profile",
        headers=_headers("user-a"),
        json={
            "display_name": "  Voyage Alice  ",
            "bio": "  Loves noodles.  ",
            "home_city": "  Xiamen  ",
            "travel_styles": ["美食", "自然"],
            "avatar_path": f"{USER_A}/avatar/avatar.webp",
        },
    )

    assert response.status_code == 200
    assert set(response.json()) == {
        "user_id",
        "email",
        "display_name",
        "bio",
        "home_city",
        "travel_styles",
        "avatar_url",
        "updated_at",
    }
    assert response.json()["display_name"] == "Voyage Alice"
    assert response.json()["bio"] == "Loves noodles."
    assert response.json()["home_city"] == "Xiamen"
    assert response.json()["travel_styles"] == ["美食", "自然"]
    assert response.json()["avatar_url"] == (
        f"https://signed.example.test/{USER_A}/avatar/avatar.webp"
    )
    assert "avatar_path" not in response.json()

    stored = profile_module.get_profile(
        AuthenticatedUser(id=USER_A, email="alice@example.com")
    )
    assert stored.display_name == "Voyage Alice"


def test_profile_routes_require_a_bearer_token(client: TestClient):
    response = client.get("/api/profile")

    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "AUTH_REQUIRED"


def test_get_profile_storage_outage_returns_stable_503(client: TestClient):
    from app.composition import get_profile_module

    class FailingQuery:
        def select(self, _columns):
            return self

        def eq(self, _field, _value):
            return self

        def execute(self):
            raise RuntimeError("vendor get failure with private details")

    class Client:
        def table(self, name):
            assert name == "profiles"
            return FailingQuery()

    app.dependency_overrides[get_profile_module] = lambda: ProfileModule(
        SupabaseProfileRepository(Client())
    )
    try:
        with TestClient(app, raise_server_exceptions=False) as outage_client:
            response = outage_client.get(
                "/api/profile", headers=_headers("user-a")
            )
    finally:
        app.dependency_overrides.pop(get_profile_module, None)

    assert response.status_code == 503
    assert response.json()["detail"] == {
        "code": "PROFILE_UNAVAILABLE",
        "message": "Profile service unavailable",
    }
    assert "vendor" not in response.text.lower()


def test_put_profile_storage_outage_returns_stable_503(client: TestClient):
    from app.composition import get_profile_module

    class Query:
        def __init__(self):
            self.operation = None

        def select(self, _columns):
            self.operation = "select"
            return self

        def eq(self, _field, _value):
            return self

        def upsert(self, _row, *, on_conflict):
            assert on_conflict == "user_id"
            self.operation = "upsert"
            return self

        def execute(self):
            if self.operation == "select":
                return type("Response", (), {"data": []})()
            raise RuntimeError("vendor replace failure with private details")

    class Client:
        def table(self, name):
            assert name == "profiles"
            return Query()

    app.dependency_overrides[get_profile_module] = lambda: ProfileModule(
        SupabaseProfileRepository(Client())
    )
    try:
        with TestClient(app, raise_server_exceptions=False) as outage_client:
            response = outage_client.put(
                "/api/profile",
                headers=_headers("user-a"),
                json={
                    "display_name": "Alice",
                    "bio": "",
                    "home_city": "",
                    "travel_styles": [],
                },
            )
    finally:
        app.dependency_overrides.pop(get_profile_module, None)

    assert response.status_code == 503
    assert response.json()["detail"] == {
        "code": "PROFILE_UNAVAILABLE",
        "message": "Profile service unavailable",
    }
    assert "vendor" not in response.text.lower()


@pytest.mark.parametrize(
    "payload",
    [
        {
            "display_name": "Alice",
            "bio": "",
            "home_city": "",
            "travel_styles": [],
            "email": "private@example.com",
        },
        {
            "display_name": "Alice",
            "bio": "",
            "home_city": "",
            "travel_styles": [],
            "user_id": str(USER_A),
        },
        {
            "display_name": "Alice",
            "bio": "",
            "home_city": "",
            "travel_styles": [],
            "trip_id": "11111111-1111-1111-1111-111111111111",
        },
        {
            "display_name": "Alice",
            "bio": "",
            "home_city": "",
            "travel_styles": [],
            "preferences": {"theme": "forest"},
        },
        {
            "display_name": "Alice",
            "bio": "",
            "home_city": "",
            "travel_styles": ["海岛"],
        },
        {
            "display_name": "Alice",
            "bio": "",
            "home_city": "",
            "travel_styles": [],
            "avatar_url": "https://cdn.example.test/avatar.webp",
        },
    ],
)
def test_put_profile_rejects_invalid_or_extra_fields(
    client: TestClient, payload: dict[str, object]
):
    response = client.put("/api/profile", headers=_headers("user-a"), json=payload)

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "REQUEST_INVALID"


def test_put_profile_rejects_avatar_path_outside_the_authenticated_users_avatar_prefix(
    client: TestClient,
):
    response = client.put(
        "/api/profile",
        headers=_headers("user-a"),
        json={
            "display_name": "Alice",
            "bio": "",
            "home_city": "",
            "travel_styles": [],
            "avatar_path": "22222222-2222-2222-2222-222222222222/avatar/other.webp",
        },
    )

    assert response.status_code == 422
    assert response.json()["detail"] == {
        "code": "PROFILE_VALIDATION_FAILED",
        "message": "Profile request validation failed",
    }
