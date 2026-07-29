import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(monkeypatch):
    """Serve the actual app shell without making any external request."""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    from app.core.config import get_settings
    from app.main import app

    get_settings.cache_clear()
    with TestClient(app) as test_client:
        yield test_client
    get_settings.cache_clear()
