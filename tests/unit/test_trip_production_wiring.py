from uuid import UUID

from app.api.auth import AuthenticatedUser
from app.core.config import get_settings
from app.infrastructure.repositories import InMemoryTripRepository


USER_A = UUID("11111111-1111-1111-1111-111111111111")


def test_production_service_uses_verified_bearer_for_jwt_scoped_repository(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "anon-test-key")
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "unused-by-trips")
    get_settings.cache_clear()
    from app.trips import service as service_module

    seen = []
    monkeypatch.setattr(
        service_module,
        "create_user_scoped_supabase_repository",
        lambda token: seen.append(token) or InMemoryTripRepository(),
    )
    service_module.get_trip_service.cache_clear()

    service_module.get_trip_service(
        AuthenticatedUser(id=USER_A, email="a@example.com", access_token="verified-jwt")
    )

    assert seen == ["verified-jwt"]
    get_settings.cache_clear()
    service_module.get_trip_service.cache_clear()


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
