import secrets
from uuid import UUID

from app.api.auth import AuthenticatedUser
from app.core.config import get_settings
from app.travel_notes.moderation import TravelNoteModerationModule


ADMIN_ID = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")


def test_production_moderation_wiring_uses_bearer_and_internal_signing_client(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "anon-test-key")
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "service-test-key")
    monkeypatch.setenv("ANON_SESSION_SIGNING_SECRET", secrets.token_urlsafe(32))
    get_settings.cache_clear()

    from app import composition

    seen: dict[str, object] = {}

    class StubRepository:
        pass

    monkeypatch.setattr(
        composition,
        "create_user_scoped_moderation_repository",
        lambda url, key, token, **kwargs: seen.update(
            url=url, key=key, token=token, kwargs=kwargs
        )
        or StubRepository(),
    )
    composition.get_development_community_moderation_module.cache_clear()

    module = composition.get_community_moderation_module(
        AuthenticatedUser(
            id=ADMIN_ID,
            email="admin@example.com",
            access_token="verified-admin-jwt",
        )
    )

    assert isinstance(module, TravelNoteModerationModule)
    assert seen["url"] == "https://example.supabase.co/"
    assert seen["key"] == "anon-test-key"
    assert seen["token"] == "verified-admin-jwt"
    assert "media_gateway" in seen["kwargs"]
    assert "internal_client" in seen["kwargs"]

    composition.get_development_community_moderation_module.cache_clear()
    get_settings.cache_clear()
