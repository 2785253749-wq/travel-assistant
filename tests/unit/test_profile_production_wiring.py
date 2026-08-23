from datetime import UTC, datetime
from pathlib import Path
import logging
import secrets
from uuid import UUID

import pytest

from app.api.auth import AuthenticatedUser
from app.core.config import get_settings
from app.core.errors import AppError
from app.profile.repositories import (
    InMemoryProfileRepository,
    SupabaseProfileRepository,
)
from app.travel_notes.media import NoopCommunityMediaCleanupQueue


USER_A = UUID("11111111-1111-1111-1111-111111111111")


def test_profile_module_has_no_fastapi_config_or_infrastructure_dependencies():
    source = Path("app/profile/service.py").read_text(encoding="utf-8")

    assert "app.api" not in source
    assert "app.core.config" not in source
    assert "app.infrastructure" not in source
    assert "fastapi" not in source.lower()


def _clear_profile_state(service_module):
    for name in (
        "get_profile_module",
        "get_development_profile_repository",
        "get_development_profile_module",
    ):
        dependency = getattr(service_module, name, None)
        if dependency is not None and hasattr(dependency, "cache_clear"):
            dependency.cache_clear()


def test_production_profile_module_uses_verified_bearer_for_jwt_scoped_repository(
    monkeypatch,
):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "anon-test-key")
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "unused-by-profiles")
    monkeypatch.setenv("ANON_SESSION_SIGNING_SECRET", secrets.token_urlsafe(32))
    get_settings.cache_clear()
    from app import composition as service_module

    seen = []
    repository = InMemoryProfileRepository()
    repository.seed(
        user_id=USER_A,
        display_name="Voyage Alice",
        preferences={"bio": "", "home_city": "", "travel_styles": []},
        avatar_path=f"{USER_A}/avatar/avatar.webp",
        updated_at=datetime(2026, 8, 20, 8, 0, tzinfo=UTC),
    )
    monkeypatch.setattr(
        service_module,
        "create_user_scoped_profile_repository",
        lambda _url, _key, token: seen.append(token) or repository,
    )
    monkeypatch.setattr(
        service_module,
        "get_community_media_gateway",
        lambda: type(
            "Gateway",
            (),
            {
                "sign_paths": staticmethod(
                    lambda paths, expires_in=None: [
                        f"https://signed.example.test/{path}" for path in paths
                    ]
                )
            },
        )(),
    )
    monkeypatch.setattr(
        service_module,
        "get_community_media_cleanup_queue",
        lambda: NoopCommunityMediaCleanupQueue(),
    )
    _clear_profile_state(service_module)

    profile = service_module.get_profile_module(
        AuthenticatedUser(id=USER_A, email="alice@example.com", access_token="verified-jwt")
    ).get_profile(
        AuthenticatedUser(id=USER_A, email="alice@example.com", access_token="verified-jwt")
    )

    assert seen == ["verified-jwt"]
    assert profile.avatar_url == f"https://signed.example.test/{USER_A}/avatar/avatar.webp"
    get_settings.cache_clear()
    _clear_profile_state(service_module)


def test_same_verified_token_does_not_reuse_jwt_scoped_profile_repository(
    monkeypatch,
):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "anon-test-key")
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "unused-by-profiles")
    monkeypatch.setenv("ANON_SESSION_SIGNING_SECRET", secrets.token_urlsafe(32))
    get_settings.cache_clear()
    from app import composition as service_module

    repositories = [InMemoryProfileRepository(), InMemoryProfileRepository()]
    monkeypatch.setattr(
        service_module,
        "create_user_scoped_profile_repository",
        lambda _url, _key, _token: repositories.pop(0),
    )
    monkeypatch.setattr(
        service_module,
        "get_community_media_gateway",
        lambda: type(
            "Gateway",
            (),
            {"sign_paths": staticmethod(lambda paths, expires_in=None: list(paths))},
        )(),
    )
    monkeypatch.setattr(
        service_module,
        "get_community_media_cleanup_queue",
        lambda: NoopCommunityMediaCleanupQueue(),
    )
    _clear_profile_state(service_module)
    user = AuthenticatedUser(
        id=USER_A, email="alice@example.com", access_token="same-verified-jwt"
    )

    first = service_module.get_profile_module(user)
    second = service_module.get_profile_module(user)

    assert first is not second
    assert repositories == []
    get_settings.cache_clear()
    _clear_profile_state(service_module)


def test_supabase_profile_repository_logs_hashed_subject_and_uses_profiles_table(
    caplog,
):
    class Query:
        def select(self, columns):
            assert columns == "user_id, display_name, preferences, avatar_path, updated_at"
            return self

        def eq(self, field, value):
            assert field == "user_id"
            assert value == str(USER_A)
            return self

        def execute(self):
            return type(
                "Response",
                (),
                {
                    "data": [
                        {
                            "user_id": str(USER_A),
                            "display_name": " Voyage Alice ",
                            "preferences": {
                                "bio": " Loves noodles. ",
                                "home_city": " Xiamen ",
                                "travel_styles": ["美食", "自然"],
                            },
                            "avatar_path": f"{USER_A}/avatar/avatar.webp",
                            "updated_at": datetime(2026, 8, 20, 12, 0, tzinfo=UTC).isoformat(),
                        }
                    ]
                },
            )()

    class Client:
        def table(self, name):
            assert name == "profiles"
            return Query()

    with caplog.at_level(logging.INFO, logger="app.database"):
        profile = SupabaseProfileRepository(Client()).get(USER_A)

    assert profile is not None
    record = next(record for record in caplog.records if record.message == "database_result")
    assert record.subject.startswith("user-digest:")
    assert str(USER_A) not in caplog.text


def test_supabase_profile_repository_normalizes_get_outages():
    class FailingQuery:
        def select(self, _columns):
            return self

        def eq(self, _field, _value):
            return self

        def execute(self):
            raise RuntimeError("vendor get failure with private details")

    class Client:
        def table(self, name):
            assert name == "profiles"
            return FailingQuery()

    with pytest.raises(AppError) as error:
        SupabaseProfileRepository(Client()).get(USER_A)

    assert error.value.code == "PROFILE_UNAVAILABLE"
    assert error.value.message == "Profile service unavailable"


def test_supabase_profile_repository_normalizes_replace_outages():
    class FailingQuery:
        def upsert(self, _row, *, on_conflict):
            assert on_conflict == "user_id"
            return self

        def execute(self):
            raise RuntimeError("vendor replace failure with private details")

    class Client:
        def table(self, name):
            assert name == "profiles"
            return FailingQuery()

    with pytest.raises(AppError) as error:
        SupabaseProfileRepository(Client()).replace(
            USER_A,
            display_name="Alice",
            preferences={"bio": "", "home_city": "", "travel_styles": []},
            avatar_path=f"{USER_A}/avatar/avatar.webp",
        )

    assert error.value.code == "PROFILE_UNAVAILABLE"
    assert error.value.message == "Profile service unavailable"
