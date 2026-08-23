from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from app.community.models import (
    COMMUNITY_AUTHOR_DISPLAY_NAME_MAX_LENGTH,
    COMMUNITY_SUMMARY_MAX_LENGTH,
    CommunityPost,
)
from app.core.errors import AppError
from app.core.logging import database_operation, hashed_log_subject
from app.profile.repositories import InMemoryProfileRepository, StoredProfile
from app.trips.models import Trip
from app.infrastructure.repositories import InMemoryTripRepository


_DEFAULT_AUTHOR_DISPLAY_NAME = "Voyage 旅行者"


@dataclass
class StoredCommunityPost:
    user_id: UUID
    source_trip_id: UUID | None
    post: CommunityPost


class InMemoryCommunityRepository:
    def __init__(
        self,
        *,
        trip_repository: InMemoryTripRepository,
        profile_repository: InMemoryProfileRepository,
    ) -> None:
        self._trip_repository = trip_repository
        self._profile_repository = profile_repository
        self.posts: dict[UUID, StoredCommunityPost] = {}

    def publish(self, user_id: UUID, trip_id: UUID, summary: str) -> CommunityPost:
        trip = self._trip_repository.get(user_id, trip_id)
        if trip is None:
            raise AppError("COMMUNITY_POST_NOT_FOUND", "Community post not found")
        if trip.status != "planned" or trip.itinerary is None:
            raise AppError(
                "COMMUNITY_TRIP_NOT_PUBLISHABLE", "Trip is not publishable"
            )
        if any(
            stored.user_id == user_id and stored.source_trip_id == trip_id
            for stored in self.posts.values()
        ):
            raise AppError("COMMUNITY_POST_EXISTS", "Community post already exists")

        normalized_summary = _normalized_summary(summary)
        if normalized_summary is None:
            raise AppError(
                "COMMUNITY_VALIDATION_FAILED", "Community request validation failed"
            )

        now = datetime.now(UTC)
        post = CommunityPost(
            id=uuid4(),
            author_display_name=_display_name_for(self._profile_repository.get(user_id)),
            title=trip.title,
            destination=_trip_destination(trip),
            summary=normalized_summary,
            itinerary_snapshot=deepcopy(trip.itinerary.model_dump(mode="json")),
            created_at=now,
            updated_at=now,
            can_delete=False,
        )
        stored = StoredCommunityPost(
            user_id=user_id,
            source_trip_id=trip_id,
            post=post.model_copy(deep=True),
        )
        self.posts[stored.post.id] = stored
        return stored.post.model_copy(deep=True)

    def withdraw(self, user_id: UUID, post_id: UUID) -> bool:
        stored = self.posts.get(post_id)
        if stored is None or stored.user_id != user_id:
            return False
        del self.posts[post_id]
        return True

    def list_posts(
        self, cursor: tuple[datetime, UUID] | None, limit: int
    ) -> list[CommunityPost]:
        rows = sorted(
            (stored.post.model_copy(deep=True) for stored in self.posts.values()),
            key=lambda post: (post.created_at, str(post.id)),
            reverse=True,
        )
        if cursor is not None:
            cursor_created_at, cursor_id = cursor
            rows = [
                row
                for row in rows
                if row.created_at < cursor_created_at
                or (
                    row.created_at == cursor_created_at
                    and str(row.id) < str(cursor_id)
                )
            ]
        return rows[:limit]

    def get_post(self, post_id: UUID) -> CommunityPost | None:
        stored = self.posts.get(post_id)
        return None if stored is None else stored.post.model_copy(deep=True)

    def list_owned_post_ids(self, user_id: UUID, post_ids: list[UUID]) -> set[UUID]:
        requested = set(post_ids)
        return {
            post_id
            for post_id, stored in self.posts.items()
            if stored.user_id == user_id and post_id in requested
        }


class SupabaseCommunityRepository:
    def __init__(self, client) -> None:
        self._client = client

    def publish(self, user_id: UUID, trip_id: UUID, summary: str) -> CommunityPost:
        try:
            with database_operation(
                "community.publish", subject=hashed_log_subject("user", user_id)
            ):
                response = self._client.rpc(
                    "publish_community_post",
                    {"p_source_trip_id": str(trip_id), "p_summary": summary},
                ).execute()
            return _post_from_row(_one_row(response.data))
        except Exception as exc:  # pragma: no cover - exercised through fakes
            raise _map_community_database_error(exc) from exc

    def withdraw(self, user_id: UUID, post_id: UUID) -> bool:
        try:
            with database_operation(
                "community.withdraw", subject=hashed_log_subject("user", user_id)
            ):
                response = (
                    self._client.table("community_posts")
                    .delete()
                    .eq("id", str(post_id))
                    .eq("user_id", str(user_id))
                    .execute()
                )
            return bool(_row_list(response.data))
        except Exception as exc:  # pragma: no cover - exercised through fakes
            raise _map_community_database_error(exc) from exc

    def list_owned_post_ids(self, user_id: UUID, post_ids: list[UUID]) -> set[UUID]:
        if not post_ids:
            return set()
        try:
            with database_operation(
                "community.owned_ids", subject=hashed_log_subject("user", user_id)
            ):
                response = (
                    self._client.table("community_posts")
                    .select("id")
                    .eq("user_id", str(user_id))
                    .in_("id", [str(post_id) for post_id in post_ids])
                    .execute()
                )
            return {UUID(str(row["id"])) for row in _row_list(response.data)}
        except Exception as exc:  # pragma: no cover - exercised through fakes
            raise _map_community_database_error(exc) from exc


class SupabasePublicCommunityRepository:
    def __init__(self, client) -> None:
        self._client = client

    def list_posts(
        self, cursor: tuple[datetime, UUID] | None, limit: int
    ) -> list[CommunityPost]:
        params = {
            "cursor_created_at": cursor[0].isoformat() if cursor is not None else None,
            "cursor_id": str(cursor[1]) if cursor is not None else None,
            "page_size": limit,
        }
        try:
            with database_operation("community.list_public"):
                response = self._client.rpc("list_community_posts", params).execute()
            return [_post_from_row(row) for row in _row_list(response.data)]
        except Exception as exc:  # pragma: no cover - exercised through fakes
            raise _map_community_database_error(exc) from exc

    def get_post(self, post_id: UUID) -> CommunityPost | None:
        try:
            with database_operation(
                "community.get_public", subject=hashed_log_subject("community_post", post_id)
            ):
                response = self._client.rpc(
                    "get_community_post", {"post_id": str(post_id)}
                ).execute()
            rows = _row_list(response.data)
            if not rows:
                return None
            return _post_from_row(rows[0])
        except Exception as exc:  # pragma: no cover - exercised through fakes
            raise _map_community_database_error(exc) from exc


def create_user_scoped_community_repository(
    url: str, anon_key: str, access_token: str
) -> SupabaseCommunityRepository:
    from supabase import create_client

    client = create_client(url, anon_key)
    client.postgrest.auth(access_token)
    return SupabaseCommunityRepository(client)


def create_public_community_repository(
    url: str, anon_key: str
) -> SupabasePublicCommunityRepository:
    from supabase import create_client

    client = create_client(url, anon_key)
    return SupabasePublicCommunityRepository(client)


def _trip_destination(trip: Trip) -> str:
    destination = (trip.profile.destination or "").strip()
    if not destination:
        raise AppError("COMMUNITY_TRIP_NOT_PUBLISHABLE", "Trip is not publishable")
    return destination


def _display_name_for(profile: StoredProfile | None) -> str:
    if profile is None or not isinstance(profile.display_name, str):
        return _DEFAULT_AUTHOR_DISPLAY_NAME
    normalized = profile.display_name.strip() or _DEFAULT_AUTHOR_DISPLAY_NAME
    return normalized[:COMMUNITY_AUTHOR_DISPLAY_NAME_MAX_LENGTH]


def _normalized_summary(summary: str) -> str | None:
    normalized = summary.strip()
    if not 1 <= len(normalized) <= COMMUNITY_SUMMARY_MAX_LENGTH:
        return None
    return normalized


def _one_row(data: object) -> dict:
    rows = _row_list(data, allow_single_object=True)
    if rows:
        return rows[0]
    raise RuntimeError("community operation returned no row")


def _row_list(data: object, *, allow_single_object: bool = False) -> list[dict]:
    if allow_single_object and isinstance(data, dict):
        return [data]
    if not isinstance(data, list):
        raise RuntimeError("community operation returned an invalid row set")
    if not all(isinstance(row, dict) for row in data):
        raise RuntimeError("community operation returned an invalid row set")
    return data


def _post_from_row(row: dict) -> CommunityPost:
    return CommunityPost.model_validate(
        {
            "id": row["id"],
            "author_display_name": row["author_display_name"],
            "title": row["title"],
            "destination": row["destination"],
            "summary": row["summary"],
            "itinerary_snapshot": deepcopy(row["itinerary_snapshot"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "can_delete": False,
        }
    )


def _map_community_database_error(error: Exception) -> AppError:
    message = str(error)
    code = str(getattr(error, "code", "") or "").lower()
    lowered = message.lower()
    if code == "23505" or "duplicate community post" in lowered:
        return AppError("COMMUNITY_POST_EXISTS", "Community post already exists")
    if code == "p0002" or "trip not found" in lowered:
        return AppError("COMMUNITY_POST_NOT_FOUND", "Community post not found")
    if "not publishable" in lowered or "destination is required" in lowered:
        return AppError("COMMUNITY_TRIP_NOT_PUBLISHABLE", "Trip is not publishable")
    if code == "p0001" or "summary must be" in lowered:
        return AppError(
            "COMMUNITY_VALIDATION_FAILED", "Community request validation failed"
        )
    return AppError("COMMUNITY_PUBLISH_FAILED", "Community publish failed")
