import pytest


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "uses_dotenv: allow an explicit test to exercise the real dotenv source",
    )


@pytest.fixture(autouse=True)
def configured_test_model(monkeypatch, request):
    """Keep ordinary tests independent from credentials in the developer's .env."""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-only-key")
    from app.core.config import get_settings

    if not request.node.get_closest_marker("uses_dotenv"):
        from app.core.config import Settings

        original_init = Settings.__init__

        def isolated_init(self, *args, **kwargs):
            kwargs.setdefault("_env_file", None)
            original_init(self, *args, **kwargs)

        monkeypatch.setattr(Settings, "__init__", isolated_init)

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
