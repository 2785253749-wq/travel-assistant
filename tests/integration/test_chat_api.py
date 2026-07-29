from fastapi.testclient import TestClient


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
