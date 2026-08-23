from __future__ import annotations

from collections.abc import Mapping
import logging
from typing import Protocol
from uuid import UUID

from app.core.errors import AppError
from app.profile.models import ProfileInput, UserProfile
from app.profile.repositories import (
    InMemoryProfileRepository,
    StoredProfile,
    SupabaseProfileRepository,
)
from app.schemas import ALLOWED_TRAVEL_STYLES
from app.travel_notes.media import NoopCommunityMediaCleanupQueue
from app.core.logging import hashed_log_subject, operational_context


PROFILE_MANAGED_KEYS = frozenset({"bio", "home_city", "travel_styles"})


class ProfileUser(Protocol):
    id: UUID
    email: str | None


class ProfileRepository(Protocol):
    def get(self, user_id: UUID) -> StoredProfile | None: ...
    def replace(
        self,
        user_id: UUID,
        *,
        display_name: str,
        preferences: dict[str, object],
        avatar_path: str | None,
    ) -> StoredProfile: ...


class ProfileMediaGateway(Protocol):
    def sign_paths(
        self, paths: list[str], expires_in: int | None = None
    ) -> list[str]: ...


class ProfileCleanupQueue(Protocol):
    def enqueue(
        self,
        paths: list[str],
        *,
        note_id: UUID | None = None,
        image_id: UUID | None = None,
    ) -> int: ...


class _IdentityProfileMediaGateway:
    def sign_paths(
        self, paths: list[str], expires_in: int | None = None
    ) -> list[str]:
        del expires_in
        return list(paths)


class ProfileModule:
    def __init__(
        self,
        repository: ProfileRepository,
        *,
        media_gateway: ProfileMediaGateway | None = None,
        cleanup_queue: ProfileCleanupQueue | None = None,
    ) -> None:
        self._repository = repository
        self._media_gateway = media_gateway or _IdentityProfileMediaGateway()
        self._cleanup_queue = cleanup_queue or NoopCommunityMediaCleanupQueue()

    def get_profile(self, user: ProfileUser) -> UserProfile:
        stored = self._repository.get(user.id)
        if stored is None:
            return UserProfile(
                user_id=user.id,
                email=user.email,
                display_name="",
                bio="",
                home_city="",
                travel_styles=[],
                avatar_url=None,
                updated_at=None,
            )
        return self._to_user_profile(user, stored)

    def replace_profile(
        self, user: ProfileUser, profile_input: ProfileInput
    ) -> UserProfile:
        current = self._repository.get(user.id)
        current_preferences = _preference_object(current.preferences) if current else {}
        current_avatar_path = _normalized_avatar_path(current.avatar_path, user.id) if current else None
        next_preferences = {
            **{
                key: value
                for key, value in current_preferences.items()
                if key not in PROFILE_MANAGED_KEYS
            },
            "bio": profile_input.bio,
            "home_city": profile_input.home_city,
            "travel_styles": list(profile_input.travel_styles),
        }
        if "avatar_path" in profile_input.model_fields_set:
            next_avatar_path = _validated_avatar_path(user.id, profile_input.avatar_path)
        else:
            next_avatar_path = current_avatar_path
        stored = self._repository.replace(
            user.id,
            display_name=profile_input.display_name,
            preferences=next_preferences,
            avatar_path=next_avatar_path,
        )
        if current_avatar_path and current_avatar_path != next_avatar_path:
            self._enqueue_cleanup(current_avatar_path)
        return self._to_user_profile(user, stored)

    def _to_user_profile(self, user: ProfileUser, stored: StoredProfile) -> UserProfile:
        preferences = _preference_object(stored.preferences)
        try:
            avatar_url = self._signed_avatar_url(stored.avatar_path, user.id)
        except Exception as exc:  # pragma: no cover - exercised via fakes
            raise _unavailable() from exc
        return UserProfile(
            user_id=user.id,
            email=user.email,
            display_name=_normalized_text(stored.display_name, max_length=40),
            bio=_normalized_text(preferences.get("bio"), max_length=160),
            home_city=_normalized_text(preferences.get("home_city"), max_length=40),
            travel_styles=_normalized_travel_styles(preferences.get("travel_styles")),
            avatar_url=avatar_url,
            updated_at=stored.updated_at,
        )

    def _signed_avatar_url(self, raw_path: object, user_id: UUID) -> str | None:
        avatar_path = _normalized_avatar_path(raw_path, user_id)
        if avatar_path is None:
            return None
        signed_paths = self._media_gateway.sign_paths([avatar_path])
        if len(signed_paths) != 1 or not signed_paths[0]:
            raise RuntimeError("profile avatar signing returned an invalid payload")
        return signed_paths[0]

    def _enqueue_cleanup(self, storage_path: str) -> None:
        try:
            self._cleanup_queue.enqueue([storage_path])
        except Exception as exc:  # pragma: no cover - exercised via fakes
            logging.getLogger("app.community_media").warning(
                "cleanup_enqueue_failed",
                extra=operational_context(
                    subject=hashed_log_subject("community-media", storage_path),
                    stage="profile-avatar",
                    error_code="COMMUNITY_MEDIA_CLEANUP_ENQUEUE_FAILED",
                    exception_type=type(exc).__name__,
                ),
            )


def _preference_object(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping):
        return {}
    return {str(key): item for key, item in value.items()}


def _normalized_text(value: object, *, max_length: int) -> str:
    if not isinstance(value, str):
        return ""
    normalized = value.strip()
    if len(normalized) > max_length:
        return ""
    return normalized


def _normalized_travel_styles(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    if len(value) > 5:
        return []
    if not all(isinstance(item, str) and item in ALLOWED_TRAVEL_STYLES for item in value):
        return []
    return list(value)


def _normalized_avatar_path(value: object, user_id: UUID) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    if not normalized:
        return None
    prefix = f"{user_id}/avatar/"
    if not normalized.startswith(prefix):
        return None
    return normalized


def _validated_avatar_path(user_id: UUID, value: str | None) -> str | None:
    if value is None:
        return None
    normalized = _normalized_avatar_path(value, user_id)
    if normalized is None:
        raise _validation_failed()
    return normalized


def _validation_failed() -> AppError:
    return AppError(
        "PROFILE_VALIDATION_FAILED",
        "Profile request validation failed",
    )


def _unavailable() -> AppError:
    return AppError("PROFILE_UNAVAILABLE", "Profile service unavailable")


__all__ = [
    "InMemoryProfileRepository",
    "ProfileModule",
    "SupabaseProfileRepository",
]
