from fastapi.testclient import TestClient


def _collect_complete(client: TestClient, thread_id: str) -> None:
    response = client.post(
        "/api/chat",
        json={
            "message": "\u4ece\u4e0a\u6d77\u5230\u676d\u5dde 2026-10-01 \u81f3 2026-10-02 2\u4eba \u9884\u7b973000\u5143",
            "thread_id": thread_id,
            "action": "collect",
        },
    )
    assert response.status_code == 200
    assert response.json()["stage"] == "confirming"


def test_daily_limit_is_a_safe_429_with_request_id(monkeypatch):
    from app import composition
    from app.core.errors import AppError
    from app.main import app

    class Guard:
        def reserve(self, subject):
            raise AppError("AI_DAILY_LIMIT_REACHED", "daily limit reached")

    client = TestClient(app)
    _collect_complete(client, "thread-limit")
    monkeypatch.setattr(composition, "get_usage_guard", lambda: Guard())
    response = client.post(
        "/api/chat", headers={"X-Request-ID": "request-8"},
        json={"message": "confirm", "thread_id": "thread-limit", "action": "confirm"},
    )

    assert response.status_code == 429
    assert response.headers["X-Request-ID"] == "request-8"
    assert response.json()["detail"]["code"] == "AI_DAILY_LIMIT_REACHED"


def test_disabled_ai_is_a_safe_503_without_calling_chat(monkeypatch):
    from app import composition
    from app.core.errors import AppError
    from app.main import app

    class Guard:
        def reserve(self, subject):
            raise AppError("AI_DISABLED", "provider key=do-not-leak")

    client = TestClient(app)
    _collect_complete(client, "thread-disabled")
    monkeypatch.setattr(composition, "get_usage_guard", lambda: Guard())
    response = client.post(
        "/api/chat",
        json={"message": "confirm", "thread_id": "thread-disabled", "action": "confirm"},
    )

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "AI_DISABLED"
    assert "do-not-leak" not in response.text


def test_no_model_key_degrades_to_a_warning_without_provider_body(monkeypatch):
    from app import composition
    from app.core.usage import ProviderUnavailable
    from app.main import app

    class Guard:
        def reserve(self, subject):
            raise ProviderUnavailable("upstream body: key=do-not-leak")

    client = TestClient(app)
    _collect_complete(client, "thread-provider")
    monkeypatch.setattr(composition, "get_usage_guard", lambda: Guard())
    response = client.post(
        "/api/chat",
        json={"message": "confirm", "thread_id": "thread-provider", "action": "confirm"},
    )

    assert response.status_code == 200
    assert response.json()["warnings"] == ["AI_UNAVAILABLE"]
    assert "do-not-leak" not in response.text
