from fastapi.testclient import TestClient


def test_daily_limit_is_a_safe_429_with_request_id(monkeypatch):
    from app.api import chat as chat_api
    from app.core.errors import AppError
    from app.main import app

    class Guard:
        def reserve(self, subject):
            raise AppError("AI_DAILY_LIMIT_REACHED", "daily limit reached")

    monkeypatch.setattr(chat_api, "get_usage_guard", lambda: Guard())
    response = TestClient(app).post(
        "/api/chat", headers={"X-Request-ID": "request-8"},
        json={"message": "from Shanghai", "thread_id": "thread-8"},
    )

    assert response.status_code == 429
    assert response.headers["X-Request-ID"] == "request-8"
    assert response.json()["detail"]["code"] == "AI_DAILY_LIMIT_REACHED"


def test_disabled_ai_is_a_safe_503_without_calling_chat(monkeypatch):
    from app.api import chat as chat_api
    from app.core.errors import AppError
    from app.main import app

    class Guard:
        def reserve(self, subject):
            raise AppError("AI_DISABLED", "provider key=do-not-leak")

    monkeypatch.setattr(chat_api, "get_usage_guard", lambda: Guard())
    monkeypatch.setattr(chat_api, "chat", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("model path called")))
    response = TestClient(app).post("/api/chat", json={"message": "hello", "thread_id": "thread-8"})

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "AI_DISABLED"
    assert "do-not-leak" not in response.text


def test_no_model_key_degrades_to_a_warning_without_provider_body(monkeypatch):
    from app.api import chat as chat_api
    from app.core.usage import ProviderUnavailable
    from app.main import app

    class Guard:
        def reserve(self, subject):
            raise ProviderUnavailable("upstream body: key=do-not-leak")

    monkeypatch.setattr(chat_api, "get_usage_guard", lambda: Guard())
    response = TestClient(app).post("/api/chat", json={"message": "hello", "thread_id": "thread-8"})

    assert response.status_code == 200
    assert response.json()["warnings"] == ["AI_UNAVAILABLE"]
    assert "do-not-leak" not in response.text
