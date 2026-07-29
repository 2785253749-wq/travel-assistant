from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4
import secrets

from app.api.auth import AuthenticatedUser
from app.core.config import get_settings
from app.infrastructure.repositories import InMemoryTripRepository
from app.trips.models import ShareLink


USER_A = UUID("11111111-1111-1111-1111-111111111111")


def _clear_service_state(service_module):
    for name in ("get_trip_service", "get_public_trip_service", "get_development_repository"):
        dependency = getattr(service_module, name, None)
        if dependency is not None and hasattr(dependency, "cache_clear"):
            dependency.cache_clear()


def test_production_service_uses_verified_bearer_for_jwt_scoped_repository(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "anon-test-key")
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "unused-by-trips")
    monkeypatch.setenv("ANON_SESSION_SIGNING_SECRET", secrets.token_urlsafe(32))
    get_settings.cache_clear()
    from app.trips import service as service_module

    seen = []
    monkeypatch.setattr(
        service_module,
        "create_user_scoped_supabase_repository",
        lambda token: seen.append(token) or InMemoryTripRepository(),
    )
    _clear_service_state(service_module)

    service_module.get_trip_service(
        AuthenticatedUser(id=USER_A, email="a@example.com", access_token="verified-jwt")
    )

    assert seen == ["verified-jwt"]
    get_settings.cache_clear()
    _clear_service_state(service_module)


def test_same_verified_token_does_not_reuse_jwt_scoped_client(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "anon-test-key")
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "unused-by-trips")
    monkeypatch.setenv("ANON_SESSION_SIGNING_SECRET", secrets.token_urlsafe(32))
    get_settings.cache_clear()
    from app.trips import service as service_module

    repositories = [InMemoryTripRepository(), InMemoryTripRepository()]
    monkeypatch.setattr(
        service_module,
        "create_user_scoped_supabase_repository",
        lambda token: repositories.pop(0),
    )
    _clear_service_state(service_module)
    user = AuthenticatedUser(
        id=USER_A, email="a@example.com", access_token="same-verified-jwt"
    )

    first = service_module.get_trip_service(user)
    second = service_module.get_trip_service(user)

    assert first is not second
    assert repositories == []
    get_settings.cache_clear()
    _clear_service_state(service_module)


def test_supabase_repository_maps_created_share_link():
    from app.infrastructure.repositories import SupabaseTripRepository

    share_id = uuid4()
    trip_id = uuid4()
    expires_at = datetime.now(UTC) + timedelta(days=30)
    created_at = datetime.now(UTC)
    row = {
        "id": str(share_id),
        "user_id": str(USER_A),
        "trip_id": str(trip_id),
        "token_hash": "a" * 64,
        "expires_at": expires_at.isoformat(),
        "revoked_at": None,
        "created_at": created_at.isoformat(),
    }

    class InsertQuery:
        def insert(self, payload):
            assert payload["token_hash"] == "a" * 64
            return self

        def execute(self):
            return type("Response", (), {"data": [row]})()

    class FakeClient:
        def table(self, name):
            assert name == "share_links"
            return InsertQuery()

    stored = SupabaseTripRepository(FakeClient()).create_share_link(
        ShareLink(
            id=share_id,
            user_id=USER_A,
            trip_id=trip_id,
            token_hash="a" * 64,
            expires_at=expires_at,
        )
    )

    assert stored.id == share_id
    assert stored.user_id == USER_A
    assert stored.trip_id == trip_id
    assert stored.token_hash == "a" * 64
    assert stored.expires_at == expires_at


def test_public_share_repository_calls_only_restricted_rpc():
    from app.infrastructure.repositories import SupabasePublicShareRepository

    class RpcCall:
        def execute(self):
            return type("Response", (), {"data": [{"id": "trip"}]})()

    class RpcOnlyClient:
        def __init__(self):
            self.calls = []

        def rpc(self, name, params):
            self.calls.append((name, params))
            return RpcCall()

        def table(self, _):
            raise AssertionError("public sharing must not query a base table")

    client = RpcOnlyClient()
    result = SupabasePublicShareRepository(client).get_shared_trip("hashed-token")

    assert result == {"id": "trip"}
    assert client.calls == [("get_shared_trip_by_token_hash", {"p_token_hash": "hashed-token"})]
