import pytest


@pytest.fixture(autouse=True)
def configured_test_model(monkeypatch):
    """Offline route tests use an explicit fake key; production wiring tests override it."""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-only-key")
    from app.core.config import get_settings
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
