import json
from datetime import UTC, datetime
from types import SimpleNamespace

from fastapi.testclient import TestClient
from uuid import UUID


def test_chat_api_keeps_legacy_response_shape(monkeypatch):
    from app.main import app
    from app.api import chat as chat_api
    from app.agent.graph import ChatResult

    monkeypatch.setattr(
        chat_api,
        "chat",
        lambda user, trip_id, message, **kwargs: ChatResult(
            reply="请补充目的地", stage="collecting", profile={}, issues=[], sources=[]
        ),
    )

    response = TestClient(app).post(
        "/api/chat", json={"message": "从上海出发", "thread_id": "thread-1"}
    )

    assert response.status_code == 200
    assert response.json() == {
        "reply": "请补充目的地", "stage": "collecting", "profile": {
            "origin": None, "destination": None, "start_date": None, "end_date": None,
            "travelers": None, "budget_cny": None, "preferences": [], "constraints": [],
        },
    }


def test_chat_api_bounds_and_deduplicates_generated_citations_and_warnings(monkeypatch):
    """A direct JSONResponse must not bypass the public response contract."""
    from app.main import app
    from app.api import chat as chat_api
    from app.agent.graph import ChatResult

    generated_at = datetime(2026, 8, 7, tzinfo=UTC)
    sources = [
        {
            "evidence_id": f"evidence-{index}",
            "source_url": f"https://example.com/source-{index}",
            "source_type": "official",
            "fetched_at": generated_at,
            "freshness": "reference only",
        }
        for index in range(101)
    ]
    sources.insert(1, dict(sources[0]))
    warnings = [f"warning-{index}" for index in range(41)]
    monkeypatch.setattr(
        chat_api,
        "chat",
        lambda *args, **kwargs: ChatResult(
            "ok", "collecting", {}, sources=sources, warnings=warnings
        ),
    )

    response = TestClient(app).post(
        "/api/chat", json={"message": "sources", "thread_id": "bounds"}
    )

    assert response.status_code == 200
    payload = response.json()
    assert len(payload["sources"]) == 100
    assert [source["evidence_id"] for source in payload["sources"]].count("evidence-0") == 1
    assert payload["sources"][-1]["evidence_id"] == "evidence-99"
    assert payload["warnings"] == [f"warning-{index}" for index in range(40)]


def test_chat_api_returns_safe_error_without_exception_detail(monkeypatch):
    from app.main import app
    from app.api import chat as chat_api

    def broken_chat(user, trip_id, message, **kwargs):
        raise RuntimeError("provider token secret")

    monkeypatch.setattr(chat_api, "chat", broken_chat)

    response = TestClient(app).post(
        "/api/chat", json={"message": "从上海出发", "thread_id": "thread-1"}
    )

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "CHAT_UNAVAILABLE"
    assert "secret" not in response.text


def test_chat_api_logs_only_stable_error_metadata(monkeypatch, caplog):
    from app.main import app
    from app.api import chat as chat_api

    monkeypatch.setattr(chat_api, "chat", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("Bearer jwt-secret")))
    with caplog.at_level("WARNING", logger="app.api.chat"):
        response = TestClient(app).post(
            "/api/chat", headers={"X-Request-ID": "req-agent-failure"},
            json={"message": "从北京出发", "thread_id": "thread-1"}
        )

    assert response.status_code == 503
    record = next(record for record in caplog.records if record.message == "chat_request_failed")
    assert record.error_code == "CHAT_UNAVAILABLE"
    assert record.exception_type == "RuntimeError"
    assert record.request_id == "req-agent-failure"
    assert response.json()["request_id"] == "req-agent-failure"
    assert "jwt-secret" not in caplog.text


def test_anonymous_session_cookie_scopes_same_thread_per_client(monkeypatch):
    from app.main import app
    from app.api import chat as chat_api
    from app.agent.graph import ChatResult

    profiles = {}

    def fake_chat(user, trip_id, message, *, thread_id, session_scope, quota_subject, action):
        key = (session_scope, thread_id)
        profile = dict(profiles.get(key, {}))
        if message == "first":
            profile["destination"] = "杭州"
        if message == "second":
            profile["travelers"] = 2
        profiles[key] = profile
        return ChatResult("ok", "collecting", profile)

    monkeypatch.setattr(chat_api, "chat", fake_chat)
    client_a = TestClient(app)
    client_b = TestClient(app)

    first_a = client_a.post("/api/chat", json={"message": "first", "thread_id": "same"})
    second_b = client_b.post("/api/chat", json={"message": "second", "thread_id": "same"})
    second_a = client_a.post("/api/chat", json={"message": "second", "thread_id": "same"})

    assert first_a.cookies.get("travel_session")
    assert "HttpOnly" in first_a.headers["set-cookie"]
    assert "SameSite=lax" in first_a.headers["set-cookie"]
    assert second_b.json()["profile"]["destination"] is None
    assert second_a.json()["profile"]["destination"] == "杭州"
    assert "set-cookie" not in second_a.headers
    assert len({scope for scope, _ in profiles}) == 2


def test_anonymous_quota_subject_survives_cookie_deletion_and_ignores_spoofed_forwarding(monkeypatch):
    """A caller must not receive a fresh AI allowance by dropping one cookie."""
    from app.main import app
    from app.api import chat as chat_api
    from app.agent.graph import ChatResult

    identities = []

    def fake_chat(
        user, trip_id, message, *, thread_id, session_scope, quota_subject, action
    ):
        identities.append((session_scope, quota_subject))
        return ChatResult("ok", "collecting", {})

    monkeypatch.setattr(chat_api, "chat", fake_chat)
    client = TestClient(app, client=("198.51.100.24", 51000))

    first = client.post(
        "/api/chat",
        headers={"X-Forwarded-For": "203.0.113.99"},
        json={"message": "first", "thread_id": "quota", "action": "collect"},
    )
    client.cookies.clear()
    second = client.post(
        "/api/chat",
        headers={"X-Forwarded-For": "192.0.2.88"},
        json={"message": "second", "thread_id": "quota", "action": "collect"},
    )

    assert first.status_code == second.status_code == 200
    assert identities[0][0] != identities[1][0]
    assert identities[0][1] == identities[1][1]
    assert identities[0][1].startswith("anon-network:")
    assert "198.51.100.24" not in identities[0][1]


def test_render_cloudflare_identity_is_single_value_and_fails_closed(monkeypatch):
    from app.main import app
    from app.api import chat as chat_api
    from app.agent.graph import ChatResult
    from app.core.config import get_settings

    quota_subjects = []

    def fake_chat(user, trip_id, message, **kwargs):
        quota_subjects.append(kwargs["quota_subject"])
        return ChatResult("ok", "collecting", {})

    monkeypatch.setattr(chat_api, "chat", fake_chat)
    monkeypatch.setenv("TRUSTED_CLIENT_IP_HEADER", "cf-connecting-ip")
    get_settings.cache_clear()
    clients = [
        TestClient(app, client=("10.0.0.1", 51000)),
        TestClient(app, client=("10.0.0.2", 51000)),
        TestClient(app, client=("10.0.0.3", 51000)),
        TestClient(app, client=("10.0.0.4", 51000)),
    ]
    headers = [
        {"CF-Connecting-IP": "2001:db8:1:2::10", "X-Forwarded-For": "203.0.113.1"},
        {"CF-Connecting-IP": "2001:db8:1:2::99", "X-Forwarded-For": "192.0.2.9"},
        {},
        {"CF-Connecting-IP": "bad,203.0.113.7"},
    ]
    try:
        for client, request_headers in zip(clients, headers, strict=True):
            response = client.post(
                "/api/chat",
                headers=request_headers,
                json={"message": "hello", "thread_id": "quota", "action": "collect"},
            )
            assert response.status_code == 200
    finally:
        get_settings.cache_clear()

    assert quota_subjects[0] == quota_subjects[1]
    assert quota_subjects[2] == quota_subjects[3]
    assert quota_subjects[0] != quota_subjects[2]


def test_well_formed_client_supplied_legacy_cookie_is_rotated(monkeypatch):
    from app.main import app
    from app.api import chat as chat_api
    from app.agent.graph import ChatResult

    scopes = []
    monkeypatch.setattr(
        chat_api,
        "chat",
        lambda user, trip_id, message, **kwargs: (
            scopes.append(kwargs["session_scope"])
            or ChatResult("ok", "collecting", {})
        ),
    )
    client = TestClient(app)
    forged_legacy = "A" * 43
    client.cookies.set("travel_session", forged_legacy)

    response = client.post("/api/chat", json={"message": "first", "thread_id": "same"})
    rotated = response.cookies.get("travel_session")

    assert response.status_code == 200
    assert rotated and rotated != forged_legacy
    assert "." in rotated
    assert forged_legacy not in scopes[0]


def test_well_formed_forged_signature_is_rotated(monkeypatch):
    from app.main import app
    from app.api import chat as chat_api
    from app.agent.graph import ChatResult

    monkeypatch.setattr(
        chat_api, "chat",
        lambda *args, **kwargs: ChatResult("ok", "collecting", {}),
    )
    client = TestClient(app)
    forged = f"{'A' * 43}.{'B' * 43}"
    client.cookies.set("travel_session", forged)

    response = client.post("/api/chat", json={"message": "first", "thread_id": "same"})
    rotated = response.cookies.get("travel_session")

    assert response.status_code == 200
    assert rotated and rotated != forged


def test_tampered_real_cookie_cannot_access_existing_profile(monkeypatch):
    from app.main import app
    from app.api import chat as chat_api
    from app.agent.graph import ChatResult

    profiles = {}

    def fake_chat(user, trip_id, message, *, thread_id, session_scope, quota_subject, action):
        key = (session_scope, thread_id)
        profile = dict(profiles.get(key, {}))
        if message == "first":
            profile["destination"] = "杭州"
        profiles[key] = profile
        return ChatResult("ok", "collecting", profile)

    monkeypatch.setattr(chat_api, "chat", fake_chat)
    owner = TestClient(app)
    owner_response = owner.post(
        "/api/chat", json={"message": "first", "thread_id": "same"}
    )
    genuine = owner_response.cookies.get("travel_session")
    assert genuine
    session_id, signature = genuine.split(".")
    assert all(session_id not in scope and signature not in scope for scope, _ in profiles)

    tampered = genuine[:-1] + ("A" if genuine[-1] != "A" else "B")
    attacker = TestClient(app)
    attacker.cookies.set("travel_session", tampered)
    attack_response = attacker.post(
        "/api/chat", json={"message": "second", "thread_id": "same"}
    )

    assert attack_response.status_code == 200
    assert attack_response.json()["profile"]["destination"] is None
    assert attack_response.cookies.get("travel_session") != tampered


def test_authenticated_users_scope_same_thread_by_verified_user_id(monkeypatch):
    from app.main import app
    from app.api import chat as chat_api
    from app.api.auth import AuthenticatedUser, get_optional_current_user
    from app.agent.graph import ChatResult

    scopes = []

    def fake_chat(user, trip_id, message, *, thread_id, session_scope, quota_subject, action):
        scopes.append(session_scope)
        return ChatResult("ok", "collecting", {})

    monkeypatch.setattr(chat_api, "chat", fake_chat)
    client = TestClient(app)
    try:
        app.dependency_overrides[get_optional_current_user] = lambda: AuthenticatedUser(
            UUID("11111111-1111-1111-1111-111111111111"), "a@example.test", "Bearer raw-a"
        )
        client.post("/api/chat", json={"message": "first", "thread_id": "same"})
        app.dependency_overrides[get_optional_current_user] = lambda: AuthenticatedUser(
            UUID("22222222-2222-2222-2222-222222222222"), "b@example.test", "Bearer raw-b"
        )
        client.post("/api/chat", json={"message": "second", "thread_id": "same"})
    finally:
        app.dependency_overrides.clear()

    assert scopes == [
        "user:11111111-1111-1111-1111-111111111111",
        "user:22222222-2222-2222-2222-222222222222",
    ]
    assert all("Bearer" not in scope for scope in scopes)


def test_invalid_bearer_is_not_downgraded_to_anonymous(monkeypatch):
    from app.main import app
    from app.api.auth import get_supabase_auth_gateway_factory
    from app.infrastructure.supabase import InvalidAuthToken

    class InvalidGateway:
        def get_user(self, access_token):
            raise InvalidAuthToken("invalid raw bearer")

    app.dependency_overrides[get_supabase_auth_gateway_factory] = lambda: lambda: InvalidGateway()
    try:
        response = TestClient(app).post(
            "/api/chat",
            headers={"Authorization": "Bearer invalid"},
            json={"message": "first", "thread_id": "same"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "AUTH_INVALID"


def test_complete_profile_stops_at_confirmation_without_reserving_or_planning(monkeypatch):
    """Removing the server confirmation branch must spend AI quota before consent."""
    from app.main import app
    from app.api import chat as chat_api
    from app.agent.graph import ChatResult

    calls = []

    def collect_only(user, trip_id, message, **kwargs):
        assert kwargs["action"] == "collect"
        calls.append("collect")
        return ChatResult(
            reply="Please confirm the trip details.",
            stage="confirming",
            profile={
                "origin": "Shanghai",
                "destination": "Hangzhou",
                "start_date": "2026-10-01",
                "end_date": "2026-10-02",
                "travelers": 2,
                "budget_cny": 3000,
                "preferences": [],
                "constraints": [],
            },
        )

    monkeypatch.setattr(chat_api, "chat", collect_only)
    response = TestClient(app).post(
        "/api/chat",
        json={"message": "complete details", "thread_id": "confirm-1", "action": "collect"},
    )

    assert response.status_code == 200
    assert response.json()["stage"] == "confirming"
    assert calls == ["collect"]


def _planned_candidate(*, budget: int, evidence_id: str, fact: str) -> dict:
    return {
        "title": "model supplied title",
        "start_date": "2026-10-01",
        "end_date": "2026-10-02",
        "days": [
            {
                "date": "2026-10-01",
                "morning": {
                    "title": "morning",
                    "start_time": "09:00",
                    "end_time": "11:00",
                    "facts": [{"text": fact, "evidence_id": evidence_id}],
                },
                "afternoon": {"title": "afternoon", "start_time": "13:00", "end_time": "15:00"},
                "evening": {"title": "evening", "start_time": "18:00", "end_time": "20:00"},
            },
            {
                "date": "2026-10-02",
                "morning": {"title": "morning", "start_time": "09:00", "end_time": "11:00"},
                "afternoon": {"title": "afternoon", "start_time": "13:00", "end_time": "15:00"},
                "evening": {"title": "evening", "start_time": "18:00", "end_time": "20:00"},
            },
        ],
        "budget": {
            "transport": budget,
            "hotel": 0,
            "food": 0,
            "tickets": 0,
            "reserve": 0,
            "other": 0,
            "total": budget,
            "currency": "CNY",
            "traveler_basis": "trip_total",
            "traveler_count": 2,
            "trip_total": budget,
            "estimate": {
                "low": budget,
                "point": budget,
                "high": budget,
                "currency": "CNY",
                "basis": "trip_total",
                "assumption_id": "budget-1",
            },
        },
        "notes": [],
        "assumptions": [
            {
                "assumption_id": "budget-1",
                "category": "budget",
                "description": "Confirmed trip budget.",
            }
        ],
    }


def test_http_production_path_plans_persists_reopens_modifies_and_degrades(monkeypatch, caplog):
    """Bypassing production composition must lose provider evidence or persisted plans."""
    from app.agent.graph import ModelIntentClassifier, ModelTravelExtractor, TrustedEvidence
    from app.agent.intent import IntentResult
    from app.api.auth import AuthenticatedUser, get_current_user, get_optional_current_user
    from app.main import app
    from app.providers.base import ProviderResult
    from app.providers.free_weather import WeatherProvider
    from app.providers.places import PlacesProvider
    from app.schemas import TravelProfile
    from app.composition import get_development_repository

    user = AuthenticatedUser(
        UUID("11111111-1111-1111-1111-111111111111"),
        "owner@example.test",
        "verified-token",
    )
    app.dependency_overrides[get_optional_current_user] = lambda: user
    app.dependency_overrides[get_current_user] = lambda: user
    repository = get_development_repository()
    repository.trips.clear()
    repository.messages.clear()
    repository.share_links.clear()

    base_profile = TravelProfile(
        origin="\u4e0a\u6d77",
        destination="\u676d\u5dde",
        start_date="2026-10-01",
        end_date="2026-10-02",
        travelers=2,
        budget_cny=3000,
    )
    revised_profile = base_profile.model_copy(update={"budget_cny": 3200})
    monkeypatch.setattr(
        ModelIntentClassifier,
        "classify",
        lambda self, message, has_trip: IntentResult(
            intent="modify_trip" if has_trip else "plan_trip", confidence=1.0
        ),
    )
    monkeypatch.setattr(
        ModelTravelExtractor,
        "extract",
        lambda self, message, profile: revised_profile if "3200" in message else base_profile,
    )

    fetched_at = datetime.now(UTC)
    evidence_id = "weather-http-1"
    fact = "Trusted forecast evidence."
    provider_calls = []
    monkeypatch.setattr(
        WeatherProvider,
        "forecast",
        lambda self, destination, start, end: (
            provider_calls.append(("weather", destination))
            or ProviderResult(
                data={"forecast": "fixture"},
                source="https://api.open-meteo.com/v1/forecast",
                fetched_at=fetched_at,
                evidence=(
                    TrustedEvidence(
                        evidence_id,
                        fact,
                        "https://api.open-meteo.com/v1/forecast",
                        "trusted_provider",
                        fetched_at,
                    ),
                ),
            )
        ),
    )
    monkeypatch.setattr(
        PlacesProvider,
        "search",
        lambda self, city, query: (
            provider_calls.append(("places", city))
            or ProviderResult(
                data=[],
                source="https://photon.komoot.io/api/",
                fetched_at=fetched_at,
                degraded=True,
                error_code="PLACES_TIMEOUT",
            )
        ),
    )

    candidates = iter(
        [
            _planned_candidate(budget=3000, evidence_id=evidence_id, fact=fact),
            _planned_candidate(budget=3200, evidence_id=evidence_id, fact=fact),
        ]
    )

    class PlanningModel:
        def invoke(self, messages):
            return SimpleNamespace(
                content=json.dumps(next(candidates)),
                usage_metadata={"input_tokens": 7, "output_tokens": 11},
            )

    monkeypatch.setattr("app.agent.graph.model", lambda: PlanningModel())

    client = TestClient(app)
    try:
        collected = client.post(
            "/api/chat",
            json={
                "message": "\u4ece\u4e0a\u6d77\u5230\u676d\u5dde 2026-10-01 \u81f3 2026-10-02 2\u4eba \u9884\u7b973000\u5143",
                "thread_id": "journey-1",
                "action": "collect",
            },
        )
        assert collected.status_code == 200
        assert collected.json()["stage"] == "confirming"
        assert provider_calls == []

        with caplog.at_level("INFO"):
            planned = client.post(
                "/api/chat",
                headers={"X-Request-ID": "req-http-plan"},
                json={"message": "confirm", "thread_id": "journey-1", "action": "confirm"},
            )
        assert planned.status_code == 200
        first = planned.json()
        assert first["stage"] == "planned"
        assert first["warnings"] == ["PLACES_TIMEOUT"]
        assert first["sources"][0]["evidence_id"] == evidence_id
        assert first["itinerary"]["budget"]["trip_total"] == 3000
        assert first["trip_id"]
        journey_records = [
            record
            for record in caplog.records
            if record.message in {"planning_started", "provider_result", "model_usage", "request_complete"}
            and getattr(record, "request_id", None) == "req-http-plan"
        ]
        assert {record.message for record in journey_records} == {
            "planning_started", "provider_result", "model_usage", "request_complete"
        }
        assert all(record.subject.startswith("user-digest:") for record in journey_records)
        assert all(str(user.id) not in record.subject for record in journey_records)

        reopened = client.get(f"/api/trips/{first['trip_id']}")
        assert reopened.status_code == 200
        assert reopened.json()["itinerary"] == first["itinerary"]

        modified = client.post(
            "/api/chat",
            json={
                "message": "\u9884\u7b97\u6539\u4e3a3200\u5143",
                "thread_id": "journey-1",
                "trip_id": first["trip_id"],
                "action": "collect",
            },
        )
        assert modified.status_code == 200
        assert modified.json()["stage"] == "confirming"
        assert modified.json()["profile"]["budget_cny"] == 3200
        assert len(provider_calls) == 2

        replanned = client.post(
            "/api/chat",
            json={
                "message": "confirm",
                "thread_id": "journey-1",
                "trip_id": first["trip_id"],
                "action": "confirm",
            },
        )
        assert replanned.status_code == 200
        assert replanned.json()["trip_id"] == first["trip_id"]
        assert replanned.json()["itinerary"]["budget"]["trip_total"] == 3200
        assert client.get(f"/api/trips/{first['trip_id']}").json()["itinerary"]["budget"]["trip_total"] == 3200
        assert provider_calls == [
            ("weather", "\u676d\u5dde"),
            ("places", "\u676d\u5dde"),
            ("weather", "\u676d\u5dde"),
            ("places", "\u676d\u5dde"),
        ]
    finally:
        app.dependency_overrides.clear()
        repository.trips.clear()
        repository.messages.clear()
        repository.share_links.clear()
