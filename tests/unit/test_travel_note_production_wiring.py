from __future__ import annotations

from pathlib import Path
import secrets
from uuid import UUID

import pytest

from app.api.auth import AuthenticatedUser
from app.core.config import get_settings
from app.travel_notes.service import TravelNoteModule


USER_A = UUID("11111111-1111-1111-1111-111111111111")


class _PrivateRepositoryStub:
    def create_draft(self, *args, **kwargs):
        raise AssertionError("not used in wiring test")

    def replace_draft(self, *args, **kwargs):
        raise AssertionError("not used in wiring test")

    def attach_image(self, *args, **kwargs):
        raise AssertionError("not used in wiring test")

    def remove_image(self, *args, **kwargs):
        raise AssertionError("not used in wiring test")

    def get_owned(self, *args, **kwargs):
        return None

    def get_note(self, *args, **kwargs):
        return None

    def submit(self, *args, **kwargs):
        raise AssertionError("not used in wiring test")

    def soft_delete(self, *args, **kwargs):
        raise AssertionError("not used in wiring test")

    def list_owned(self, *args, **kwargs):
        return []

    def get_source_trip_snapshot(self, *args, **kwargs):
        return None

    def approve(self, *args, **kwargs):
        raise AssertionError("not used in wiring test")

    def reject(self, *args, **kwargs):
        raise AssertionError("not used in wiring test")


class _PublicRepositoryStub:
    def list_public(self, cursor, limit, *, category, search_query):
        del cursor, limit, category, search_query
        return []

    def get_public(self, note_id):
        del note_id
        return None


class _MediaGatewayStub:
    def sign_paths(self, paths):
        return list(paths)


def test_travel_note_domain_service_has_no_fastapi_config_or_infrastructure_dependencies():
    source = Path("app/travel_notes/service.py").read_text(encoding="utf-8")

    assert "app.api" not in source
    assert "app.core.config" not in source
    assert "app.infrastructure" not in source
    assert "fastapi" not in source.lower()


def _clear_travel_note_state(service_module):
    for name in (
        "get_travel_note_module",
        "get_optional_travel_note_module",
        "get_development_travel_note_repository",
        "get_development_travel_note_module",
        "get_public_travel_note_repository",
        "get_travel_note_media_gateway",
    ):
        dependency = getattr(service_module, name, None)
        if dependency is not None and hasattr(dependency, "cache_clear"):
            dependency.cache_clear()


def test_production_travel_note_module_uses_verified_bearer_and_service_key_public_dependencies(
    monkeypatch,
):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "anon-test-key")
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "service-test-key")
    monkeypatch.setenv("ANON_SESSION_SIGNING_SECRET", secrets.token_urlsafe(32))
    get_settings.cache_clear()
    from app import composition as service_module

    seen_private_tokens: list[str] = []
    seen_public_args: list[tuple[str, str]] = []
    seen_media_args: list[tuple[str, str]] = []
    monkeypatch.setattr(
        service_module,
        "create_user_scoped_travel_note_repository",
        lambda _url, _key, token, **_kwargs: seen_private_tokens.append(token)
        or _PrivateRepositoryStub(),
    )
    monkeypatch.setattr(
        service_module,
        "create_public_travel_note_repository",
        lambda url, key, **_kwargs: seen_public_args.append((url, key))
        or _PublicRepositoryStub(),
    )
    monkeypatch.setattr(
        service_module,
        "create_travel_note_media_gateway",
        lambda url, key, **_kwargs: seen_media_args.append((url, key))
        or _MediaGatewayStub(),
    )
    _clear_travel_note_state(service_module)

    module = service_module.get_travel_note_module(
        AuthenticatedUser(id=USER_A, email="alice@example.com", access_token="verified-jwt")
    )

    assert isinstance(module, TravelNoteModule)
    assert seen_private_tokens == ["verified-jwt"]
    assert seen_public_args == [("https://example.supabase.co/", "service-test-key")]
    assert seen_media_args == [("https://example.supabase.co/", "service-test-key")]
    get_settings.cache_clear()
    _clear_travel_note_state(service_module)


def test_anonymous_travel_note_reads_use_cached_service_key_dependencies(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "anon-test-key")
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "service-test-key")
    monkeypatch.setenv("ANON_SESSION_SIGNING_SECRET", secrets.token_urlsafe(32))
    get_settings.cache_clear()
    from app import composition as service_module

    public_calls: list[tuple[str, str, object]] = []
    media_calls: list[tuple[str, str, object]] = []
    monkeypatch.setattr(
        service_module,
        "create_public_travel_note_repository",
        lambda url, key, **_kwargs: public_calls.append((url, key, object()))
        or public_calls[-1][2],
    )
    monkeypatch.setattr(
        service_module,
        "create_travel_note_media_gateway",
        lambda url, key, **_kwargs: media_calls.append((url, key, object()))
        or media_calls[-1][2],
    )
    monkeypatch.setattr(
        service_module,
        "create_user_scoped_travel_note_repository",
        lambda *_args: pytest.fail(
            "anonymous public travel note reads must not require a JWT-scoped repository"
        ),
    )
    _clear_travel_note_state(service_module)

    first = service_module.get_optional_travel_note_module(None)
    second = service_module.get_optional_travel_note_module(None)

    assert isinstance(first, TravelNoteModule)
    assert isinstance(second, TravelNoteModule)
    assert len(public_calls) == 1
    assert len(media_calls) == 1
    assert public_calls[0][:2] == ("https://example.supabase.co/", "service-test-key")
    assert media_calls[0][:2] == ("https://example.supabase.co/", "service-test-key")
    assert first._public_repository is second._public_repository
    assert first._media_gateway is second._media_gateway
    get_settings.cache_clear()
    _clear_travel_note_state(service_module)


def test_same_verified_token_does_not_reuse_jwt_scoped_travel_note_repository(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "anon-test-key")
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "service-test-key")
    monkeypatch.setenv("ANON_SESSION_SIGNING_SECRET", secrets.token_urlsafe(32))
    get_settings.cache_clear()
    from app import composition as service_module

    repositories = [_PrivateRepositoryStub(), _PrivateRepositoryStub()]
    monkeypatch.setattr(
        service_module,
        "create_user_scoped_travel_note_repository",
        lambda _url, _key, _token, **_kwargs: repositories.pop(0),
    )
    monkeypatch.setattr(
        service_module,
        "create_public_travel_note_repository",
        lambda _url, _key, **_kwargs: _PublicRepositoryStub(),
    )
    monkeypatch.setattr(
        service_module,
        "create_travel_note_media_gateway",
        lambda _url, _key, **_kwargs: _MediaGatewayStub(),
    )
    _clear_travel_note_state(service_module)
    user = AuthenticatedUser(
        id=USER_A, email="alice@example.com", access_token="same-verified-jwt"
    )

    first = service_module.get_travel_note_module(user)
    second = service_module.get_travel_note_module(user)

    assert first is not second
    assert repositories == []
    get_settings.cache_clear()
    _clear_travel_note_state(service_module)
