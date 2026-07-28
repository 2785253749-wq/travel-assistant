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
