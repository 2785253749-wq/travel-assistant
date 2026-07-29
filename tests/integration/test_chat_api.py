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
            "/api/chat", json={"message": "从北京出发", "thread_id": "thread-1"}
        )

    assert response.status_code == 503
    record = next(record for record in caplog.records if record.message == "chat_request_failed")
    assert record.error_code == "CHAT_UNAVAILABLE"
    assert record.exception_type == "RuntimeError"
    assert "jwt-secret" not in caplog.text


def test_anonymous_session_cookie_scopes_same_thread_per_client(monkeypatch):
    from app.main import app
    from app.api import chat as chat_api
    from app.agent.graph import ChatResult

    profiles = {}

    def fake_chat(user, trip_id, message, *, thread_id, session_scope):
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
    assert len({scope for scope, _ in profiles}) == 2


def test_authenticated_users_scope_same_thread_by_verified_user_id(monkeypatch):
    from app.main import app
    from app.api import chat as chat_api
    from app.api.auth import AuthenticatedUser, get_optional_current_user
    from app.agent.graph import ChatResult

    scopes = []

    def fake_chat(user, trip_id, message, *, thread_id, session_scope):
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
