from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4
import secrets

import pytest
from pydantic import ValidationError

from app.api.auth import AuthenticatedUser
from app.core.config import get_settings
from app.infrastructure.repositories import InMemoryTripRepository, SupabaseTripRepository
from app.schemas import Itinerary, TravelProfile
from app.trips.models import ConversationMessage, ShareLink, Trip


USER_A = UUID("11111111-1111-1111-1111-111111111111")


def test_trip_domain_service_has_no_fastapi_config_or_infrastructure_dependencies():
    source = Path("app/trips/service.py").read_text(encoding="utf-8")

    assert "app.api" not in source
    assert "app.core.config" not in source
    assert "app.infrastructure" not in source
    assert "fastapi" not in source.lower()


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
    from app import composition as service_module

    seen = []
    monkeypatch.setattr(
        service_module,
        "create_user_scoped_supabase_repository",
        lambda _url, _key, token: seen.append(token) or InMemoryTripRepository(),
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
    from app import composition as service_module

    repositories = [InMemoryTripRepository(), InMemoryTripRepository()]
    monkeypatch.setattr(
        service_module,
        "create_user_scoped_supabase_repository",
        lambda _url, _key, _token: repositories.pop(0),
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


def test_supabase_repository_isolates_legacy_invalid_trip_rows_on_reads():
    valid_id = uuid4()
    invalid_row = {
        "id": str(uuid4()),
        "user_id": str(USER_A),
        "title": "legacy invalid",
        "status": "planned",
        "profile": {},
        "itinerary": None,
    }
    valid_row = {
        "id": str(valid_id),
        "user_id": str(USER_A),
        "title": "valid collecting trip",
        "status": "collecting",
        "profile": {},
        "itinerary": None,
    }
    invalid_title_row = {
        "id": str(uuid4()),
        "user_id": str(USER_A),
        "title": "x" * 101,
        "status": "collecting",
        "profile": {},
        "itinerary": None,
    }

    class Query:
        def __init__(self, rows):
            self.rows = rows

        def select(self, _columns):
            return self

        def eq(self, _field, _value):
            return self

        def order(self, _field, *, desc):
            assert desc is True
            return self

        def execute(self):
            return type("Response", (), {"data": self.rows})()

    class FakeClient:
        def __init__(self, rows):
            self.rows = rows

        def table(self, name):
            assert name == "trips"
            return Query(self.rows)

    listed = SupabaseTripRepository(
        FakeClient([invalid_row, invalid_title_row, valid_row])
    ).list_for_user(USER_A)
    fetched = SupabaseTripRepository(FakeClient([invalid_row])).get(
        USER_A, UUID(invalid_row["id"])
    )
    title_fetched = SupabaseTripRepository(FakeClient([invalid_title_row])).get(
        USER_A, UUID(invalid_title_row["id"])
    )

    assert [trip.id for trip in listed] == [valid_id]
    assert fetched is None
    assert title_fetched is None


def test_supabase_repository_rejects_invalid_title_before_insert():
    inserted_rows = []

    class Query:
        def insert(self, row):
            inserted_rows.append(row)
            return self

        def execute(self):
            return type("Response", (), {"data": inserted_rows})()

    class FakeClient:
        def table(self, name):
            assert name == "trips"
            return Query()

    trip = Trip(user_id=USER_A, title="valid", profile=TravelProfile())
    trip.title = "x" * 101

    with pytest.raises(ValidationError):
        SupabaseTripRepository(FakeClient()).create(trip)

    assert inserted_rows == []


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


def test_supabase_planned_chat_uses_one_atomic_rpc_instead_of_table_writes():
    trip_id = uuid4()
    itinerary = Itinerary.model_validate_json(
        Path("tests/fixtures/task7_itinerary.json").read_text(encoding="utf-8")
    )
    profile = TravelProfile(
        origin="上海",
        destination="成都",
        start_date="2026-10-01",
        end_date="2026-10-02",
        travelers=2,
        budget_cny=5000,
    )
    trip = Trip(
        id=trip_id,
        user_id=USER_A,
        title="成都 trip",
        profile=profile,
        status="planned",
        itinerary=itinerary,
    )
    user_message = ConversationMessage(
        user_id=USER_A,
        trip_id=trip_id,
        role="user",
        content="请规划成都行程",
    )
    assistant_message = ConversationMessage(
        user_id=USER_A,
        trip_id=trip_id,
        role="assistant",
        content="# 成都行程\n\n可读摘要",
    )
    row = {
        "id": str(trip_id),
        "user_id": str(USER_A),
        "title": trip.title,
        "status": "planned",
        "profile": profile.model_dump(mode="json"),
        "itinerary": itinerary.model_dump(mode="json"),
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
            raise AssertionError("planned chat persistence must use one transaction RPC")

    client = RpcOnlyClient()
    stored = SupabaseTripRepository(client).persist_planned_chat(
        trip,
        (user_message, assistant_message),
        create=True,
    )

    assert stored.id == trip_id
    assert len(client.calls) == 1
    name, params = client.calls[0]
    assert name == "persist_planned_chat"
    assert params["p_create"] is True
    assert params["p_trip_id"] == str(trip_id)
    assert params["p_user_message"] == "请规划成都行程"
    assert params["p_assistant_message"] == "# 成都行程\n\n可读摘要"
