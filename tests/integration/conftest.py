import pytest
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def configured_test_baidu_provider(monkeypatch):
    """Keep chat integration paths independent from a developer's local .env."""
    monkeypatch.setenv("BAIDU_MAP_AK", "test-baidu-ak")


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
