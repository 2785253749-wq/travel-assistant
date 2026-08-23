from datetime import UTC, datetime
from pathlib import Path
import secrets
from uuid import UUID, uuid4

import pytest

from app.api.auth import AuthenticatedUser
from app.community.service import (
    CommunityModule,
    SupabaseCommunityRepository,
    SupabasePublicCommunityRepository,
)
from app.core.config import get_settings


USER_A = UUID("11111111-1111-1111-1111-111111111111")
USER_B = UUID("22222222-2222-2222-2222-222222222222")


class _PrivateRepositoryStub:
    def publish(self, user_id, trip_id, summary):
        raise AssertionError("not used in wiring test")

    def withdraw(self, user_id, post_id):
        raise AssertionError("not used in wiring test")

    def list_owned_post_ids(self, user_id, post_ids):
        return set()


class _PublicRepositoryStub:
    def list_posts(self, cursor, limit):
        return []

    def get_post(self, post_id):
        return None


def test_community_domain_service_has_no_fastapi_config_or_infrastructure_dependencies():
    source = Path("app/community/service.py").read_text(encoding="utf-8")

    assert "app.api" not in source
    assert "app.core.config" not in source
    assert "app.infrastructure" not in source
    assert "fastapi" not in source.lower()


def _clear_community_state(service_module):
    for name in (
        "get_optional_community_module",
        "get_community_module",
        "get_development_community_repository",
        "get_development_community_module",
        "get_public_community_repository",
    ):
        dependency = getattr(service_module, name, None)
        if dependency is not None and hasattr(dependency, "cache_clear"):
            dependency.cache_clear()


def test_production_community_module_uses_verified_bearer_for_jwt_scoped_repository(
    monkeypatch,
):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "anon-test-key")
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "unused-by-community")
    monkeypatch.setenv("ANON_SESSION_SIGNING_SECRET", secrets.token_urlsafe(32))
    get_settings.cache_clear()
    from app import composition as service_module

    seen = []
    monkeypatch.setattr(
        service_module,
        "create_user_scoped_community_repository",
        lambda _url, _key, token: seen.append(token) or _PrivateRepositoryStub(),
    )
    monkeypatch.setattr(
        service_module,
        "create_public_community_repository",
        lambda _url, _key: _PublicRepositoryStub(),
    )
    _clear_community_state(service_module)

    service_module.get_community_module(
        AuthenticatedUser(id=USER_A, email="alice@example.com", access_token="verified-jwt")
    )

    assert seen == ["verified-jwt"]
    get_settings.cache_clear()
    _clear_community_state(service_module)


def test_anonymous_community_reads_use_the_public_anon_repository(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "anon-test-key")
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "unused-by-community")
    monkeypatch.setenv("ANON_SESSION_SIGNING_SECRET", secrets.token_urlsafe(32))
    get_settings.cache_clear()
    from app import composition as service_module

    seen = []
    monkeypatch.setattr(
        service_module,
        "create_public_community_repository",
        lambda url, key: seen.append((url, key)) or _PublicRepositoryStub(),
    )
    monkeypatch.setattr(
        service_module,
        "create_user_scoped_community_repository",
        lambda *_args: pytest.fail("anonymous community reads must not require a JWT-scoped repository"),
    )
    _clear_community_state(service_module)

    module = service_module.get_optional_community_module(None)

    assert isinstance(module, CommunityModule)
    assert seen == [("https://example.supabase.co/", "anon-test-key")]
    get_settings.cache_clear()
    _clear_community_state(service_module)


def test_same_verified_token_does_not_reuse_jwt_scoped_community_repository(
    monkeypatch,
):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "anon-test-key")
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "unused-by-community")
    monkeypatch.setenv("ANON_SESSION_SIGNING_SECRET", secrets.token_urlsafe(32))
    get_settings.cache_clear()
    from app import composition as service_module

    repositories = [_PrivateRepositoryStub(), _PrivateRepositoryStub()]
    monkeypatch.setattr(
        service_module,
        "create_user_scoped_community_repository",
        lambda _url, _key, _token: repositories.pop(0),
    )
    monkeypatch.setattr(
        service_module,
        "create_public_community_repository",
        lambda _url, _key: _PublicRepositoryStub(),
    )
    _clear_community_state(service_module)
    user = AuthenticatedUser(
        id=USER_A, email="alice@example.com", access_token="same-verified-jwt"
    )

    first = service_module.get_community_module(user)
    second = service_module.get_community_module(user)

    assert first is not second
    assert repositories == []
    get_settings.cache_clear()
    _clear_community_state(service_module)


def test_supabase_community_repository_publish_uses_only_the_allowed_rpc():
    post_id = uuid4()
    row = {
        "id": str(post_id),
        "author_display_name": "Voyage Alice",
        "title": "厦门行程",
        "destination": "厦门",
        "summary": "海边散步",
        "itinerary_snapshot": {"days": []},
        "created_at": datetime(2026, 8, 20, 9, 0, tzinfo=UTC).isoformat(),
        "updated_at": datetime(2026, 8, 20, 9, 0, tzinfo=UTC).isoformat(),
    }

    class RpcCall:
        def execute(self):
            return type("Response", (), {"data": [row]})()

    class RpcOnlyClient:
        def __init__(self):
            self.calls = []

        def rpc(self, name, params):
            self.calls.append((name, params))
            return RpcCall()

        def table(self, _name):
            raise AssertionError("community publication must use the RPC allow-list")

    client = RpcOnlyClient()
    post = SupabaseCommunityRepository(client).publish(USER_A, post_id, "海边散步")

    assert post.id == post_id
    assert client.calls == [
        (
            "publish_community_post",
            {"p_source_trip_id": str(post_id), "p_summary": "海边散步"},
        )
    ]


def test_supabase_community_repository_normalizes_malformed_publish_response_to_stable_unavailable_error():
    class RpcCall:
        def execute(self):
            return type("Response", (), {"data": []})()

    class Client:
        def rpc(self, _name, _params):
            return RpcCall()

        def table(self, _name):
            raise AssertionError("publish parsing stays on the RPC path")

    with pytest.raises(Exception) as error:
        SupabaseCommunityRepository(Client()).publish(USER_A, uuid4(), "摘要")

    assert getattr(error.value, "code", None) == "COMMUNITY_PUBLISH_FAILED"


@pytest.mark.parametrize(
    ("code", "message", "expected"),
    [
        ("23505", "duplicate community post", "COMMUNITY_POST_EXISTS"),
        ("P0002", "trip not found", "COMMUNITY_POST_NOT_FOUND"),
        ("P0001", "trip is not publishable", "COMMUNITY_TRIP_NOT_PUBLISHABLE"),
    ],
)
def test_supabase_community_repository_maps_database_errors_to_stable_domain_codes(
    code: str, message: str, expected: str
):
    class FakeDatabaseError(Exception):
        def __init__(self):
            super().__init__(message)
            self.code = code

    class FailingRpc:
        def execute(self):
            raise FakeDatabaseError()

    class Client:
        def rpc(self, _name, _params):
            return FailingRpc()

        def table(self, _name):
            raise AssertionError("publish failures should happen through the RPC path")

    with pytest.raises(Exception) as error:
        SupabaseCommunityRepository(Client()).publish(USER_A, uuid4(), "摘要")

    assert getattr(error.value, "code", None) == expected


def test_supabase_public_community_repository_calls_only_public_rpcs():
    source = Path("app/community/repositories.py").read_text(encoding="utf-8")
    assert 'table("community_posts")' not in source.split(
        "class SupabasePublicCommunityRepository", maxsplit=1
    )[1]

    row = {
        "id": str(uuid4()),
        "author_display_name": "Voyage Alice",
        "title": "厦门行程",
        "destination": "厦门",
        "summary": "海边散步",
        "itinerary_snapshot": {"days": []},
        "created_at": datetime(2026, 8, 20, 9, 0, tzinfo=UTC).isoformat(),
        "updated_at": datetime(2026, 8, 20, 9, 0, tzinfo=UTC).isoformat(),
    }

    class RpcCall:
        def __init__(self, rows):
            self._rows = rows

        def execute(self):
            return type("Response", (), {"data": self._rows})()

    class RpcOnlyClient:
        def __init__(self):
            self.calls = []

        def rpc(self, name, params):
            self.calls.append((name, params))
            if name == "list_community_posts":
                return RpcCall([row])
            if name == "get_community_post":
                return RpcCall([row])
            raise AssertionError(f"unexpected RPC {name}")

        def table(self, _name):
            raise AssertionError("public community reads must not query community_posts directly")

    client = RpcOnlyClient()
    repository = SupabasePublicCommunityRepository(client)

    listed = repository.list_posts(None, 20)
    fetched = repository.get_post(UUID(row["id"]))

    assert [post.id for post in listed] == [UUID(row["id"])]
    assert fetched is not None
    assert fetched.id == UUID(row["id"])
    assert client.calls == [
        (
            "list_community_posts",
            {"cursor_created_at": None, "cursor_id": None, "page_size": 20},
        ),
        ("get_community_post", {"post_id": row["id"]}),
    ]


def test_supabase_public_community_repository_normalizes_public_rpc_transport_failures():
    class FailingRpc:
        def execute(self):
            raise RuntimeError("temporary outage")

    class Client:
        def rpc(self, _name, _params):
            return FailingRpc()

        def table(self, _name):
            raise AssertionError("public reads must stay on RPCs")

    repository = SupabasePublicCommunityRepository(Client())

    with pytest.raises(Exception) as list_error:
        repository.list_posts(None, 20)
    with pytest.raises(Exception) as detail_error:
        repository.get_post(uuid4())

    assert getattr(list_error.value, "code", None) == "COMMUNITY_PUBLISH_FAILED"
    assert getattr(detail_error.value, "code", None) == "COMMUNITY_PUBLISH_FAILED"


def test_supabase_community_repository_normalizes_private_delete_and_owned_id_outages():
    class FailingQuery:
        def delete(self):
            return self

        def select(self, _columns):
            return self

        def eq(self, _field, _value):
            return self

        def in_(self, _field, _values):
            return self

        def execute(self):
            raise RuntimeError("temporary outage")

    class Client:
        def table(self, name):
            assert name == "community_posts"
            return FailingQuery()

        def rpc(self, _name, _params):
            raise AssertionError("delete and owned-id lookup should use table access")

    repository = SupabaseCommunityRepository(Client())

    with pytest.raises(Exception) as withdraw_error:
        repository.withdraw(USER_A, uuid4())
    with pytest.raises(Exception) as owned_error:
        repository.list_owned_post_ids(USER_A, [uuid4()])

    assert getattr(withdraw_error.value, "code", None) == "COMMUNITY_PUBLISH_FAILED"
    assert getattr(owned_error.value, "code", None) == "COMMUNITY_PUBLISH_FAILED"


def test_development_media_gateway_does_not_echo_storage_paths_in_signed_urls():
    from app import composition as service_module

    service_module.get_development_community_media_gateway.cache_clear()
    gateway = service_module.get_development_community_media_gateway()
    path = "user-a/note-a/private-cover.webp"
    signed_url = gateway.sign_paths([path])[0]

    assert signed_url.startswith("https://signed.example.test/object-")
    assert path not in signed_url
    service_module.get_development_community_media_gateway.cache_clear()
