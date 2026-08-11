import pytest
import secrets
import base64
from typing import get_type_hints

from pydantic import SecretStr


RAG_WEATHER_ENVIRONMENT_VARIABLES = (
    "APP_ENV",
    "JINA_API_KEY",
    "AMAP_WEB_SERVICE_KEY",
    "AMAP_JS_KEY",
    "AMAP_SECURITY_JS_CODE",
    "RAG_EMBEDDING_MODEL",
    "RAG_SIMILARITY_THRESHOLD",
    "RAG_DAILY_EMBEDDING_LIMIT",
    "WEATHER_DAILY_LIMIT",
    "WEATHER_CACHE_SECONDS",
    "WEATHER_TIMEOUT_SECONDS",
)


def _clear_rag_weather_environment(monkeypatch):
    for environment_variable in RAG_WEATHER_ENVIRONMENT_VARIABLES:
        monkeypatch.delenv(environment_variable, raising=False)


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


def test_amap_direct_mode_credentials_are_optional_browser_configuration(monkeypatch):
    from app.core.config import Settings

    monkeypatch.setenv("AMAP_JS_KEY", "amap-browser-test-key")
    monkeypatch.setenv("AMAP_SECURITY_JS_CODE", "amap-browser-test-security-code")

    settings = Settings(_env_file=None)

    assert settings.amap_js_key is not None
    assert settings.amap_js_key.get_secret_value() == "amap-browser-test-key"
    assert settings.amap_security_js_code is not None
    assert (
        settings.amap_security_js_code.get_secret_value()
        == "amap-browser-test-security-code"
    )


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


def test_production_accepts_missing_optional_rag_and_weather_keys(monkeypatch):
    """Guards graceful degradation when the optional providers are unconfigured."""
    from app.core.config import Settings

    _clear_rag_weather_environment(monkeypatch)
    settings = Settings(
        app_env="production",
        supabase_url="https://project.supabase.co",
        supabase_anon_key=secrets.token_urlsafe(16),
        supabase_service_key=secrets.token_urlsafe(16),
        anon_session_signing_secret=secrets.token_urlsafe(32),
        _env_file=None,
    )

    assert settings.jina_api_key is None
    assert settings.amap_web_service_key is None


def test_optional_provider_keys_are_secret_values(monkeypatch):
    """Guards provider credentials from becoming plain-text settings values."""
    from app.core.config import Settings

    _clear_rag_weather_environment(monkeypatch)
    settings = Settings(
        jina_api_key=secrets.token_urlsafe(16),
        amap_web_service_key=secrets.token_urlsafe(16),
        _env_file=None,
    )

    assert isinstance(settings.jina_api_key, SecretStr)
    assert isinstance(settings.amap_web_service_key, SecretStr)


@pytest.mark.parametrize(
    "field_name, invalid_value",
    [
        ("rag_daily_embedding_limit", 0),
        ("weather_daily_limit", 0),
        ("weather_cache_seconds", 0),
        ("weather_timeout_seconds", 0.0),
    ],
)
def test_rag_and_weather_operational_limits_must_be_positive(
    monkeypatch, field_name, invalid_value
):
    """Guards disabled rate limits or timeouts caused by non-positive settings."""
    from app.core.config import Settings

    _clear_rag_weather_environment(monkeypatch)
    with pytest.raises(ValueError):
        Settings(_env_file=None, **{field_name: invalid_value})


def test_rag_and_weather_settings_expose_the_exact_operational_contract(monkeypatch):
    """Guards type or optionality regressions in later-task configuration."""
    from app.core.config import Settings

    for environment_variable in RAG_WEATHER_ENVIRONMENT_VARIABLES:
        monkeypatch.setenv(environment_variable, "configured")
    _clear_rag_weather_environment(monkeypatch)
    settings = Settings(_env_file=None)

    annotations = get_type_hints(Settings)
    assert {
        field_name: annotations[field_name]
        for field_name in (
            "jina_api_key",
            "amap_web_service_key",
            "rag_embedding_model",
            "rag_similarity_threshold",
            "rag_daily_embedding_limit",
            "weather_daily_limit",
            "weather_cache_seconds",
            "weather_timeout_seconds",
        )
    } == {
        "jina_api_key": SecretStr | None,
        "amap_web_service_key": SecretStr | None,
        "rag_embedding_model": str,
        "rag_similarity_threshold": float,
        "rag_daily_embedding_limit": int,
        "weather_daily_limit": int,
        "weather_cache_seconds": int,
        "weather_timeout_seconds": float,
    }
    assert settings.jina_api_key is None
    assert settings.amap_web_service_key is None
    assert settings.rag_embedding_model == "jina-embeddings-v3"
    assert 0.0 <= settings.rag_similarity_threshold <= 1.0
    assert settings.rag_daily_embedding_limit > 0
    assert settings.weather_daily_limit > 0
    assert settings.weather_cache_seconds > 0
    assert settings.weather_timeout_seconds > 0


@pytest.mark.parametrize("invalid_threshold", [-0.01, 1.01])
def test_rag_similarity_threshold_must_stay_within_closed_unit_interval(
    monkeypatch,
    invalid_threshold,
):
    """Guards retrieval from accepting an impossible similarity score cutoff."""
    from app.core.config import Settings

    _clear_rag_weather_environment(monkeypatch)
    with pytest.raises(ValueError):
        Settings(_env_file=None, rag_similarity_threshold=invalid_threshold)


@pytest.mark.parametrize("allowed_threshold", [0.0, 1.0])
def test_rag_similarity_threshold_accepts_closed_interval_endpoints(
    monkeypatch,
    allowed_threshold,
):
    """Guards strict bounds from accidentally excluding valid cutoff endpoints."""
    from app.core.config import Settings

    _clear_rag_weather_environment(monkeypatch)
    assert (
        Settings(_env_file=None, rag_similarity_threshold=allowed_threshold)
        .rag_similarity_threshold
        == allowed_threshold
    )


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
