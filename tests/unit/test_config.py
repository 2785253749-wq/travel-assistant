import pytest


def test_production_requires_supabase(monkeypatch):
    from app.core.config import Settings

    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_ANON_KEY", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_KEY", raising=False)

    with pytest.raises(ValueError, match="SUPABASE_URL"):
        Settings(_env_file=None)


def test_development_uses_safe_defaults_without_supabase(monkeypatch):
    from app.core.config import Settings

    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.delenv("SUPABASE_URL", raising=False)

    settings = Settings(_env_file=None)

    assert settings.ai_user_daily_limit == 5
    assert settings.ai_global_daily_limit == 100
    assert settings.supabase_url is None
