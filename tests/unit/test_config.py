import pytest
import secrets
import base64


def _base64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def test_production_requires_supabase(monkeypatch):
    from app.core.config import Settings

    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_ANON_KEY", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_KEY", raising=False)

    with pytest.raises(ValueError, match="SUPABASE_URL"):
        Settings(_env_file=None)


@pytest.mark.parametrize("empty_setting", ["SUPABASE_ANON_KEY", "SUPABASE_SERVICE_KEY"])
def test_production_rejects_blank_supabase_keys(monkeypatch, empty_setting):
    from app.core.config import Settings

    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setenv("SUPABASE_URL", "https://project.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "anon-key")
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "service-key")
    monkeypatch.setenv(empty_setting, "   ")

    with pytest.raises(ValueError, match=empty_setting):
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


def test_amap_javascript_key_is_optional_browser_configuration(monkeypatch):
    from app.core.config import Settings

    monkeypatch.setenv("AMAP_JS_KEY", "amap-browser-test-key")

    settings = Settings(_env_file=None)

    assert settings.amap_js_key is not None
    assert settings.amap_js_key.get_secret_value() == "amap-browser-test-key"


@pytest.mark.parametrize("secret", [" x" + "A" * 43, "A" * 44 + "=", "x" * 43, "AA==", "MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA"])
def test_production_rejects_noncanonical_or_weak_session_secrets(monkeypatch, secret):
    from app.core.config import Settings
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setenv("SUPABASE_URL", "https://project.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "anon-key")
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "service-key")
    monkeypatch.setenv("ANON_SESSION_SIGNING_SECRET", secret)
    with pytest.raises(ValueError, match="ANON_SESSION_SIGNING_SECRET"):
        Settings(_env_file=None)


def test_production_accepts_a_canonical_random_base64url_session_secret(monkeypatch):
    from app.core.config import Settings
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setenv("SUPABASE_URL", "https://project.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "anon-key")
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "service-key")
    monkeypatch.setenv("ANON_SESSION_SIGNING_SECRET", secrets.token_urlsafe(32))
    assert Settings(_env_file=None).app_env == "production"


@pytest.mark.parametrize(
    "decoded",
    [
        b"abcdefghijk" * 3,
        b"a" * 16 + b"bcdefghijklmnopq",
        b"replace-me-with-a-secure-secret!!",
    ],
)
def test_production_rejects_periodic_low_entropy_or_decoded_placeholder_secrets(monkeypatch, decoded):
    from app.core.config import Settings

    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setenv("SUPABASE_URL", "https://project.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "anon-key")
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "service-key")
    monkeypatch.setenv("ANON_SESSION_SIGNING_SECRET", _base64url(decoded))

    with pytest.raises(ValueError, match="ANON_SESSION_SIGNING_SECRET"):
        Settings(_env_file=None)
