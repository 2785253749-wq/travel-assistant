from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from app.core.errors import AppError
from app.core.logging import database_operation, hashed_log_subject


@dataclass
class StoredProfile:
    user_id: UUID
    display_name: object
    preferences: object
    avatar_path: object = None
    updated_at: datetime | None = None


class InMemoryProfileRepository:
    def __init__(self) -> None:
        self.rows: dict[UUID, dict[str, object]] = {}

    def get(self, user_id: UUID) -> StoredProfile | None:
        row = self.rows.get(user_id)
        if row is None:
            return None
        return StoredProfile(
            user_id=user_id,
            display_name=row.get("display_name"),
            preferences=deepcopy(row.get("preferences", {})),
            avatar_path=row.get("avatar_path"),
            updated_at=row.get("updated_at"),
        )

    def replace(
        self,
        user_id: UUID,
        *,
        display_name: str,
        preferences: dict[str, object],
        avatar_path: str | None,
    ) -> StoredProfile:
        now = datetime.now(UTC)
        self.rows[user_id] = {
            "display_name": display_name,
            "preferences": deepcopy(preferences),
            "avatar_path": avatar_path,
            "updated_at": now,
        }
        return StoredProfile(
            user_id=user_id,
            display_name=display_name,
            preferences=deepcopy(preferences),
            avatar_path=avatar_path,
            updated_at=now,
        )

    def seed(
        self,
        *,
        user_id: UUID,
        display_name: object,
        preferences: object,
        avatar_path: object = None,
        updated_at: datetime | None,
    ) -> None:
        self.rows[user_id] = {
            "display_name": display_name,
            "preferences": deepcopy(preferences),
            "avatar_path": avatar_path,
            "updated_at": updated_at,
        }


class SupabaseProfileRepository:
    def __init__(self, client) -> None:
        self._client = client

    def get(self, user_id: UUID) -> StoredProfile | None:
        try:
            with database_operation(
                "profile.get", subject=hashed_log_subject("user", user_id)
            ):
                response = (
                    self._client.table("profiles")
                    .select("user_id, display_name, preferences, avatar_path, updated_at")
                    .eq("user_id", str(user_id))
                    .execute()
                )
                if not response.data:
                    return None
                return self._stored_from_row(response.data[0])
        except Exception as exc:  # pragma: no cover - exercised through fakes
            raise AppError(
                "PROFILE_UNAVAILABLE", "Profile service unavailable"
            ) from exc

    def replace(
        self,
        user_id: UUID,
        *,
        display_name: str,
        preferences: dict[str, object],
        avatar_path: str | None,
    ) -> StoredProfile:
        try:
            with database_operation(
                "profile.replace", subject=hashed_log_subject("user", user_id)
            ):
                response = (
                    self._client.table("profiles")
                    .upsert(
                        {
                            "user_id": str(user_id),
                            "display_name": display_name,
                            "preferences": preferences,
                            "avatar_path": avatar_path,
                        },
                        on_conflict="user_id",
                    )
                    .execute()
                )
                row = response.data[0] if response.data else None
                if not isinstance(row, dict):
                    raise RuntimeError("profile upsert returned no row")
                return self._stored_from_row(row)
        except Exception as exc:  # pragma: no cover - exercised through fakes
            raise AppError(
                "PROFILE_UNAVAILABLE", "Profile service unavailable"
            ) from exc

    @staticmethod
    def _stored_from_row(row: object) -> StoredProfile:
        if not isinstance(row, dict):
            raise RuntimeError("profile row is invalid")
        updated_at = row.get("updated_at")
        parsed_updated_at = (
            datetime.fromisoformat(updated_at)
            if isinstance(updated_at, str)
            else updated_at
        )
        return StoredProfile(
            user_id=UUID(str(row["user_id"])),
            display_name=row.get("display_name"),
            preferences=deepcopy(row.get("preferences", {})),
            avatar_path=row.get("avatar_path"),
            updated_at=parsed_updated_at,
        )


def create_user_scoped_profile_repository(
    url: str, anon_key: str, access_token: str
) -> SupabaseProfileRepository:
    from supabase import create_client

    client = create_client(url, anon_key)
    client.postgrest.auth(access_token)
    return SupabaseProfileRepository(client)
