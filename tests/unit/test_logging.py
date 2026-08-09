import json
import logging

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    from app.main import app

    return TestClient(app)


def test_request_log_contains_correlation_fields(caplog, client):
    response = client.get("/health", headers={"X-Request-ID": "req-fixed"})

    record = next(record for record in caplog.records if getattr(record, "request_id", None))
    assert response.headers["X-Request-ID"] == "req-fixed"
    assert record.request_id == "req-fixed"
    assert record.method == "GET"
    assert record.path == "/health"
    assert record.status_code == 200
    assert isinstance(record.duration_ms, float)
    assert not hasattr(record, "deepseek_api_key")


def test_json_formatter_emits_timestamp_and_operational_context_only():
    from app.core.logging import JsonFormatter

    record = logging.LogRecord("app.agent", logging.WARNING, __file__, 1, "failed", (), None)
    record.request_id = "req-1"
    record.subject = "anon-network:digest"
    record.intent = "plan_trip"
    record.provider = "weather"
    record.model_calls = 1
    record.model_input_tokens = 12
    record.model_output_tokens = 7
    record.error_code = "WEATHER_TIMEOUT"
    record.exception_type = "TimeoutError"
    record.provider_status = 401

    payload = json.loads(JsonFormatter().format(record))

    assert payload["timestamp"].endswith("Z")
    assert payload["request_id"] == "req-1"
    assert payload["subject"] == "anon-network:digest"
    assert payload["intent"] == "plan_trip"
    assert payload["provider"] == "weather"
    assert payload["model_calls"] == 1
    assert payload["model_input_tokens"] == 12
    assert payload["model_output_tokens"] == 7
    assert payload["error_code"] == "WEATHER_TIMEOUT"
    assert payload["exception_type"] == "TimeoutError"
    assert payload["provider_status"] == 401


def test_configure_logging_honors_configured_level(monkeypatch):
    from app.core.config import get_settings
    from app.core.logging import configure_logging

    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    get_settings.cache_clear()
    previous = logging.getLogger().level
    try:
        configure_logging()
        assert logging.getLogger().level == logging.DEBUG
    finally:
        logging.getLogger().setLevel(previous)


def test_shared_token_is_redacted_from_application_logs_and_error_body(caplog, client):
    token = "opaque-share-token-must-not-appear"
    with caplog.at_level(logging.INFO, logger="app.request"):
        response = client.post(
            "/api/shared/resolve",
            headers={"X-Request-ID": "req-share-redaction"},
            json={"token": token},
        )

    request_record = next(record for record in caplog.records if record.message == "request_complete")
    assert response.status_code == 404
    assert request_record.path == "/api/shared/resolve"
    assert token not in caplog.text
    assert token not in response.text
    assert response.json()["request_id"] == "req-share-redaction"


def test_validation_error_has_request_id_without_echoing_rejected_input(client):
    rejected = "private-invalid-action-value"
    response = client.post(
        "/api/chat",
        headers={"X-Request-ID": "req-validation"},
        json={"message": "hello", "thread_id": "thread-1", "action": rejected},
    )

    assert response.status_code == 422
    assert response.json() == {
        "detail": {"code": "REQUEST_INVALID", "message": "Request validation failed"},
        "request_id": "req-validation",
    }
    assert rejected not in response.text
