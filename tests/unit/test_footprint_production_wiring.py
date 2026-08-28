import secrets
from uuid import UUID

import pytest

from app.api.auth import AuthenticatedUser
from app.core.config import get_settings
from app.footprints.models import FootprintCreate
from app.footprints.repositories import InMemoryFootprintRepository
from app.footprints.service import FootprintModule


USER_A = UUID("11111111-1111-1111-1111-111111111111")
USER_B = UUID("22222222-2222-2222-2222-222222222222")


def _clear_footprint_state(composition_module) -> None:
    for name in ("get_development_footprint_repository", "get_development_footprint_module"):
        dependency = getattr(composition_module, name, None)
        if dependency is not None and hasattr(dependency, "cache_clear"):
            dependency.cache_clear()


def test_development_module_shares_memory_but_scopes_each_account(monkeypatch):
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_ANON_KEY", raising=False)
    get_settings.cache_clear()
    from app import composition as composition_module

    _clear_footprint_state(composition_module)

    first = composition_module.get_footprint_module(
        AuthenticatedUser(id=USER_A, email="alice@example.com", access_token="user-a")
    )
    second = composition_module.get_footprint_module(
        AuthenticatedUser(id=USER_B, email="bob@example.com", access_token="user-b")
    )
    first.add(USER_A, FootprintCreate(city_adcode="350200", visited_at="2025-01-02"))

    assert isinstance(first, FootprintModule)
    assert first is second
    assert [footprint.city_adcode for footprint in first.list(USER_A)] == ["350200"]
    assert second.list(USER_B) == []
    get_settings.cache_clear()
    _clear_footprint_state(composition_module)


def test_production_module_binds_the_verified_bearer_token_to_footprint_repository(
    monkeypatch,
):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "anon-test-key")
    monkeypatch.setenv("ANON_SESSION_SIGNING_SECRET", secrets.token_urlsafe(32))
    get_settings.cache_clear()
    from app import composition as composition_module

    seen_tokens: list[str] = []
    monkeypatch.setattr(
        composition_module,
        "create_user_scoped_footprint_repository",
        lambda _url, _key, token: seen_tokens.append(token)
        or InMemoryFootprintRepository(),
    )
    _clear_footprint_state(composition_module)

    module = composition_module.get_footprint_module(
        AuthenticatedUser(
            id=USER_A, email="alice@example.com", access_token="verified-jwt"
        )
    )

    assert isinstance(module, FootprintModule)
    assert seen_tokens == ["verified-jwt"]
    get_settings.cache_clear()
    _clear_footprint_state(composition_module)


def test_production_module_rejects_authenticated_users_without_a_verified_token(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "anon-test-key")
    monkeypatch.setenv("ANON_SESSION_SIGNING_SECRET", secrets.token_urlsafe(32))
    get_settings.cache_clear()
    from app import composition as composition_module

    _clear_footprint_state(composition_module)

    with pytest.raises(RuntimeError, match="verified bearer token"):
        composition_module.get_footprint_module(
            AuthenticatedUser(id=USER_A, email="alice@example.com")
        )

    get_settings.cache_clear()
    _clear_footprint_state(composition_module)
