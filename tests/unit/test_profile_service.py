from datetime import UTC, datetime
from uuid import UUID

import pytest
from pydantic import ValidationError

from app.api.auth import AuthenticatedUser
from app.core.errors import AppError
from app.profile.models import ProfileInput
from app.profile.service import InMemoryProfileRepository, ProfileModule


USER_A = UUID("11111111-1111-1111-1111-111111111111")


class _FakeMediaGateway:
    def sign_paths(self, paths: list[str], expires_in: int | None = None) -> list[str]:
        del expires_in
        return [f"https://signed.example.test/{path}" for path in paths]


class _RecordingCleanupQueue:
    def __init__(self) -> None:
        self.paths: list[str] = []

    def enqueue(self, paths: list[str], *, note_id=None, image_id=None) -> int:
        del note_id, image_id
        self.paths.extend(paths)
        return len(paths)


@pytest.fixture
def user() -> AuthenticatedUser:
    return AuthenticatedUser(id=USER_A, email="alice@example.com", access_token="jwt")


@pytest.fixture
def repository() -> InMemoryProfileRepository:
    return InMemoryProfileRepository()


@pytest.fixture
def module(repository: InMemoryProfileRepository) -> ProfileModule:
    return ProfileModule(
        repository,
        media_gateway=_FakeMediaGateway(),
        cleanup_queue=_RecordingCleanupQueue(),
    )


def test_get_profile_returns_defaults_when_profile_is_missing(
    module: ProfileModule, user: AuthenticatedUser
):
    profile = module.get_profile(user)

    assert profile.user_id == USER_A
    assert profile.email == "alice@example.com"
    assert profile.display_name == ""
    assert profile.bio == ""
    assert profile.home_city == ""
    assert profile.travel_styles == []
    assert profile.avatar_url is None
    assert profile.updated_at is None


def test_get_profile_returns_null_email_when_verified_identity_has_no_email_claim(
    module: ProfileModule,
):
    profile = module.get_profile(
        AuthenticatedUser(id=USER_A, email=None, access_token="jwt")
    )

    assert profile.user_id == USER_A
    assert profile.email is None
    assert profile.display_name == ""
    assert profile.bio == ""
    assert profile.home_city == ""
    assert profile.travel_styles == []
    assert profile.avatar_url is None
    assert profile.updated_at is None


def test_replace_profile_full_replacement_trims_fields_and_preserves_unrelated_preferences(
    module: ProfileModule,
    repository: InMemoryProfileRepository,
    user: AuthenticatedUser,
):
    repository.seed(
        user_id=USER_A,
        display_name="Legacy",
        preferences={
            "bio": "Legacy bio",
            "home_city": "Legacy city",
            "travel_styles": ["美食"],
            "theme": "forest",
            "notifications": {"marketing": False},
        },
        updated_at=datetime(2026, 8, 20, 8, 0, tzinfo=UTC),
    )

    profile = module.replace_profile(
        user,
        ProfileInput(
            display_name="  Voyage Alice  ",
            bio="  Loves noodles.  ",
            home_city="  Xiamen  ",
            travel_styles=["人文", "自然"],
        ),
    )

    assert profile.display_name == "Voyage Alice"
    assert profile.bio == "Loves noodles."
    assert profile.home_city == "Xiamen"
    assert profile.travel_styles == ["人文", "自然"]
    assert profile.updated_at is not None
    assert repository.rows[USER_A]["preferences"] == {
        "bio": "Loves noodles.",
        "home_city": "Xiamen",
        "travel_styles": ["人文", "自然"],
        "theme": "forest",
        "notifications": {"marketing": False},
    }

    replaced = module.replace_profile(
        user,
        ProfileInput(
            display_name="",
            bio="",
            home_city="",
            travel_styles=[],
        ),
    )

    assert replaced.display_name == ""
    assert replaced.bio == ""
    assert replaced.home_city == ""
    assert replaced.travel_styles == []
    assert repository.rows[USER_A]["preferences"] == {
        "bio": "",
        "home_city": "",
        "travel_styles": [],
        "theme": "forest",
        "notifications": {"marketing": False},
    }


def test_get_profile_treats_malformed_legacy_preference_values_as_empty_fields(
    module: ProfileModule, repository: InMemoryProfileRepository, user: AuthenticatedUser
):
    repository.seed(
        user_id=USER_A,
        display_name="Legacy",
        preferences={
            "bio": {"unexpected": True},
            "home_city": 123,
            "travel_styles": ["美食", "海岛"],
            "theme": "forest",
        },
        updated_at=datetime(2026, 8, 20, 9, 0, tzinfo=UTC),
    )

    profile = module.get_profile(user)

    assert profile.display_name == "Legacy"
    assert profile.bio == ""
    assert profile.home_city == ""
    assert profile.travel_styles == []


def test_get_profile_preserves_null_email_when_stored_profile_exists(
    module: ProfileModule, repository: InMemoryProfileRepository
):
    repository.seed(
        user_id=USER_A,
        display_name="Legacy",
        preferences={
            "bio": "  Loves noodles.  ",
            "home_city": "  Xiamen  ",
            "travel_styles": ["美食", "自然"],
        },
        avatar_path=f"{USER_A}/avatar/existing.webp",
        updated_at=datetime(2026, 8, 20, 9, 0, tzinfo=UTC),
    )

    profile = module.get_profile(
        AuthenticatedUser(id=USER_A, email=None, access_token="jwt")
    )

    assert profile.email is None
    assert profile.display_name == "Legacy"
    assert profile.bio == "Loves noodles."
    assert profile.home_city == "Xiamen"
    assert profile.travel_styles == ["美食", "自然"]
    assert profile.avatar_url == f"https://signed.example.test/{USER_A}/avatar/existing.webp"


def test_replace_profile_rejects_avatar_path_outside_owned_avatar_prefix(
    module: ProfileModule, user: AuthenticatedUser
):
    with pytest.raises(AppError) as error:
        module.replace_profile(
            user,
            ProfileInput(
                display_name="Alice",
                bio="",
                home_city="",
                travel_styles=[],
                avatar_path="22222222-2222-2222-2222-222222222222/avatar/not-allowed.webp",
            ),
        )

    assert error.value.code == "PROFILE_VALIDATION_FAILED"


def test_replace_profile_enqueues_previous_avatar_when_replaced(
    repository: InMemoryProfileRepository, user: AuthenticatedUser
):
    cleanup_queue = _RecordingCleanupQueue()
    module = ProfileModule(
        repository,
        media_gateway=_FakeMediaGateway(),
        cleanup_queue=cleanup_queue,
    )
    repository.seed(
        user_id=USER_A,
        display_name="Legacy",
        preferences={
            "bio": "",
            "home_city": "",
            "travel_styles": [],
        },
        avatar_path=f"{USER_A}/avatar/old.webp",
        updated_at=datetime(2026, 8, 20, 9, 0, tzinfo=UTC),
    )

    profile = module.replace_profile(
        user,
        ProfileInput(
            display_name="Voyage Alice",
            bio="",
            home_city="",
            travel_styles=[],
            avatar_path=f"{USER_A}/avatar/new.webp",
        ),
    )

    assert profile.avatar_url == f"https://signed.example.test/{USER_A}/avatar/new.webp"
    assert cleanup_queue.paths == [f"{USER_A}/avatar/old.webp"]


def test_replace_profile_rejects_invalid_profile_input_before_writing(
    module: ProfileModule, repository: InMemoryProfileRepository, user: AuthenticatedUser
):
    with pytest.raises(ValidationError):
        ProfileInput(
            display_name="ok",
            bio="",
            home_city="",
            travel_styles=["海岛"],
        )

    assert repository.rows == {}
