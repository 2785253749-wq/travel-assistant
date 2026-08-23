from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from datetime import UTC, datetime
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from app.core.errors import AppError
from app.core.logging import database_operation, hashed_log_subject
from app.travel_notes.models import TravelNoteDraftInput, TravelNoteImageInput
from app.travel_notes.ports import StoredTravelNote, StoredTravelNoteImage


COMMUNITY_MEDIA_BUCKET = "community-media"
DEFAULT_SIGNED_URL_TTL_SECONDS = 3600
_DEFAULT_AUTHOR_DISPLAY_NAME = "Voyage 旅行者"
_DEFAULT_AUTHOR_SLUG = "voyage-traveler"
# These names mirror the deployed 011 migration. Keep them centralized so the
# RPC contract cannot drift between owner and moderation operations.
_RPC_NOTE_ID_PARAM = "p_note_id"
_PUBLIC_LIST_RPC_PARAMS = (
    "cursor_published_at",
    "cursor_id",
    "page_size",
    "category_filter",
    "search_query",
)


class SupabaseTravelNoteRepository:
    def __init__(self, client, *, internal_client=None) -> None:
        self._client = client
        self._internal_client = internal_client

    def create_draft(
        self,
        user_id: UUID,
        value: TravelNoteDraftInput,
        *,
        now: datetime,
        itinerary_snapshot: dict[str, object] | None,
    ) -> StoredTravelNote:
        del now
        payload = {
            "author_id": str(user_id),
            "title": value.title,
            "body": value.body,
            "location_name": value.location_name,
            "category": value.category,
            "source_trip_id": (
                str(value.source_trip_id) if value.source_trip_id is not None else None
            ),
        }
        del itinerary_snapshot
        note_id: UUID | None = None
        try:
            with database_operation(
                "travel_note.create_draft", subject=hashed_log_subject("user", user_id)
            ):
                response = self._client.table("travel_notes").insert(payload).execute()
            note_id = _extract_note_id(response.data)
            self._insert_images(user_id, note_id, value.images)
            stored = self.get_owned(user_id, note_id)
            if stored is None:
                raise RuntimeError("travel note draft insert returned no row")
            return stored
        except Exception as exc:  # pragma: no cover - exercised through fakes
            if note_id is not None:
                self._cleanup_created_draft(user_id, note_id)
            if isinstance(exc, AppError):
                raise
            raise _map_travel_note_database_error(exc) from exc

    def replace_draft(
        self,
        user_id: UUID,
        note_id: UUID,
        value: TravelNoteDraftInput,
        *,
        now: datetime,
        itinerary_snapshot: dict[str, object] | None,
    ) -> StoredTravelNote | None:
        del now
        payload = {
            "title": value.title,
            "body": value.body,
            "location_name": value.location_name,
            "category": value.category,
            "source_trip_id": (
                str(value.source_trip_id) if value.source_trip_id is not None else None
            ),
        }
        del itinerary_snapshot
        original_row: dict[str, Any] | None = None
        original_images: list[dict[str, Any]] = []
        updated = False
        try:
            original_row = self._get_owned_row(user_id, note_id)
            if original_row is None or original_row.get("deleted_at") is not None:
                return None
            original_images = self._load_image_rows(self._client, note_id)
            with database_operation(
                "travel_note.replace_draft", subject=hashed_log_subject("user", user_id)
            ):
                response = (
                    self._client.table("travel_notes")
                    .update(payload)
                    .eq("id", str(note_id))
                    .eq("author_id", str(user_id))
                    .execute()
                )
            if not _row_list(response.data):
                return None
            updated = True
            self._replace_images(user_id, note_id, value.images, original_images)
            return self.get_owned(user_id, note_id)
        except Exception as exc:  # pragma: no cover - exercised through fakes
            if updated and original_row is not None:
                self._restore_draft(user_id, note_id, original_row, original_images)
            if isinstance(exc, AppError):
                raise
            raise _map_travel_note_database_error(exc) from exc

    def attach_image(
        self,
        user_id: UUID,
        note_id: UUID,
        image: TravelNoteImageInput,
        *,
        now: datetime,
    ) -> StoredTravelNote | None:
        del now
        payload = {
            "note_id": str(note_id),
            "owner_id": str(user_id),
            "storage_path": image.storage_path,
            "sort_order": image.sort_order,
            "width": image.width,
            "height": image.height,
        }
        try:
            with database_operation(
                "travel_note.attach_image", subject=hashed_log_subject("user", user_id)
            ):
                response = self._client.table("travel_note_images").insert(payload).execute()
            if not _row_list(response.data):
                return None
            return self.get_owned(user_id, note_id)
        except AppError:
            raise
        except Exception as exc:  # pragma: no cover - exercised through fakes
            raise _map_travel_note_database_error(exc) from exc

    def remove_image(
        self,
        user_id: UUID,
        note_id: UUID,
        image_id: UUID,
        *,
        now: datetime,
    ) -> StoredTravelNote | None:
        del now
        try:
            with database_operation(
                "travel_note.remove_image", subject=hashed_log_subject("user", user_id)
            ):
                response = (
                    self._client.table("travel_note_images")
                    .delete()
                    .eq("id", str(image_id))
                    .eq("note_id", str(note_id))
                    .eq("owner_id", str(user_id))
                    .execute()
                )
            if not _row_list(response.data):
                return None
            self._reindex_images(user_id, note_id)
            return self.get_owned(user_id, note_id)
        except AppError:
            raise
        except Exception as exc:  # pragma: no cover - exercised through fakes
            raise _map_travel_note_database_error(exc) from exc

    def get_owned(self, user_id: UUID, note_id: UUID) -> StoredTravelNote | None:
        try:
            with database_operation(
                "travel_note.get_owned", subject=hashed_log_subject("user", user_id)
            ):
                response = (
                    self._client.table("travel_notes")
                    .select("*")
                    .eq("id", str(note_id))
                    .eq("author_id", str(user_id))
                    .execute()
                )
            rows = _row_list(response.data)
            if not rows:
                return None
            stored = self._stored_from_private_row(rows[0])
            if stored.deleted_at is not None:
                return None
            return stored
        except AppError:
            raise
        except Exception as exc:  # pragma: no cover - exercised through fakes
            raise _map_travel_note_database_error(exc) from exc

    def get_note(self, note_id: UUID) -> StoredTravelNote | None:
        query_client = self._internal_client or self._client
        try:
            with database_operation(
                "travel_note.get_internal",
                subject=hashed_log_subject("travel_note", note_id),
            ):
                response = (
                    query_client.table("travel_notes")
                    .select("*")
                    .eq("id", str(note_id))
                    .execute()
                )
            rows = _row_list(response.data)
            if not rows:
                return None
            stored = self._stored_from_private_row(rows[0], query_client=query_client)
            if stored.deleted_at is not None:
                return None
            return stored
        except AppError:
            raise
        except Exception as exc:  # pragma: no cover - exercised through fakes
            raise _map_travel_note_database_error(exc) from exc

    def submit(self, user_id: UUID, note_id: UUID, *, now: datetime) -> StoredTravelNote | None:
        del now
        try:
            with database_operation(
                "travel_note.submit", subject=hashed_log_subject("user", user_id)
            ):
                response = self._client.rpc(
                    "submit_travel_note", {_RPC_NOTE_ID_PARAM: str(note_id)}
                ).execute()
            if not _row_list(response.data, allow_single_object=True):
                return None
            stored = self.get_owned(user_id, note_id)
            if stored is None:
                raise RuntimeError("travel note submit returned no owned note")
            return stored
        except AppError:
            raise
        except Exception as exc:  # pragma: no cover - exercised through fakes
            raise _map_travel_note_database_error(exc) from exc

    def soft_delete(self, user_id: UUID, note_id: UUID, *, now: datetime) -> bool:
        payload = {"deleted_at": now.isoformat()}
        try:
            with database_operation(
                "travel_note.soft_delete", subject=hashed_log_subject("user", user_id)
            ):
                response = (
                    self._client.table("travel_notes")
                    .update(payload)
                    .eq("id", str(note_id))
                    .eq("author_id", str(user_id))
                    .execute()
                )
            return bool(_row_list(response.data))
        except AppError:
            raise
        except Exception as exc:  # pragma: no cover - exercised through fakes
            raise _map_travel_note_database_error(exc) from exc

    def list_owned(self, user_id: UUID) -> list[StoredTravelNote]:
        try:
            with database_operation(
                "travel_note.list_owned", subject=hashed_log_subject("user", user_id)
            ):
                response = (
                    self._client.table("travel_notes")
                    .select("*")
                    .eq("author_id", str(user_id))
                    .order("updated_at", desc=True)
                    .order("id", desc=True)
                    .execute()
                )
            notes = [
                row
                for row in _row_list(response.data)
                if _parse_datetime(row.get("deleted_at")) is None
            ]
            return self._stored_from_private_rows(notes, query_client=self._client)
        except AppError:
            raise
        except Exception as exc:  # pragma: no cover - exercised through fakes
            raise _map_travel_note_database_error(exc) from exc

    def get_source_trip_snapshot(
        self, user_id: UUID, trip_id: UUID
    ) -> dict[str, object] | None:
        try:
            with database_operation(
                "travel_note.get_source_trip",
                subject=hashed_log_subject("user", user_id),
            ):
                response = (
                    self._client.table("trips")
                    .select("status, itinerary")
                    .eq("id", str(trip_id))
                    .eq("user_id", str(user_id))
                    .execute()
                )
            rows = _row_list(response.data)
            if not rows:
                return None
            row = rows[0]
            if row.get("status") != "planned":
                return None
            itinerary = row.get("itinerary")
            if not isinstance(itinerary, dict):
                return None
            return deepcopy(itinerary)
        except AppError:
            raise
        except Exception as exc:  # pragma: no cover - exercised through fakes
            raise _map_travel_note_database_error(exc) from exc

    def approve(self, reviewer_id: UUID, note_id: UUID, *, now: datetime) -> StoredTravelNote | None:
        del now
        try:
            with database_operation(
                "travel_note.approve", subject=hashed_log_subject("user", reviewer_id)
            ):
                response = self._client.rpc(
                    "review_travel_note",
                    {
                        _RPC_NOTE_ID_PARAM: str(note_id),
                        "decision": "approved",
                        "reason": None,
                    },
                ).execute()
            if not _row_list(response.data, allow_single_object=True):
                return None
            stored = self.get_note(note_id)
            if stored is None:
                raise RuntimeError("travel note approval returned no note")
            return stored
        except AppError:
            raise
        except Exception as exc:  # pragma: no cover - exercised through fakes
            raise _map_travel_note_database_error(exc) from exc

    def reject(
        self,
        reviewer_id: UUID,
        note_id: UUID,
        *,
        reason: str,
        now: datetime,
    ) -> StoredTravelNote | None:
        del now
        try:
            with database_operation(
                "travel_note.reject", subject=hashed_log_subject("user", reviewer_id)
            ):
                response = self._client.rpc(
                    "review_travel_note",
                    {
                        _RPC_NOTE_ID_PARAM: str(note_id),
                        "decision": "rejected",
                        "reason": reason,
                    },
                ).execute()
            if not _row_list(response.data, allow_single_object=True):
                return None
            stored = self.get_note(note_id)
            if stored is None:
                raise RuntimeError("travel note rejection returned no note")
            return stored
        except AppError:
            raise
        except Exception as exc:  # pragma: no cover - exercised through fakes
            raise _map_travel_note_database_error(exc) from exc

    def _get_owned_row(self, user_id: UUID, note_id: UUID) -> dict[str, Any] | None:
        response = (
            self._client.table("travel_notes")
            .select("*")
            .eq("id", str(note_id))
            .eq("author_id", str(user_id))
            .execute()
        )
        rows = _row_list(response.data)
        return rows[0] if rows else None

    def _stored_from_private_row(
        self, row: dict[str, Any], *, query_client=None
    ) -> StoredTravelNote:
        query_client = query_client or self._client
        author_id = UUID(str(row["author_id"]))
        images = self._load_images(query_client, UUID(str(row["id"])))
        profile = self._load_profile(query_client, author_id)
        return _stored_note_from_row(row, images=images, profile=profile)

    def _stored_from_private_rows(
        self, rows: list[dict[str, Any]], *, query_client=None
    ) -> list[StoredTravelNote]:
        if not rows:
            return []
        query_client = query_client or self._client
        note_ids = [UUID(str(row["id"])) for row in rows]
        author_ids = {UUID(str(row["author_id"])) for row in rows}
        images_by_note = self._load_images_by_note_ids(query_client, note_ids)
        profiles_by_author = self._load_profiles_by_author_ids(query_client, author_ids)
        return [
            _stored_note_from_row(
                row,
                images=images_by_note.get(UUID(str(row["id"])), ()),
                profile=profiles_by_author.get(UUID(str(row["author_id"]))),
            )
            for row in rows
        ]

    def _load_images(
        self, query_client, note_id: UUID
    ) -> tuple[StoredTravelNoteImage, ...]:
        return tuple(
            _stored_image_from_row(row)
            for row in self._load_image_rows(query_client, note_id)
        )

    def _load_image_rows(self, query_client, note_id: UUID) -> list[dict[str, Any]]:
        response = (
            query_client.table("travel_note_images")
            .select("id, note_id, owner_id, storage_path, sort_order, width, height")
            .eq("note_id", str(note_id))
            .order("sort_order", desc=False)
            .execute()
        )
        return _row_list(response.data)

    def _load_images_by_note_ids(
        self, query_client, note_ids: list[UUID]
    ) -> dict[UUID, tuple[StoredTravelNoteImage, ...]]:
        response = (
            query_client.table("travel_note_images")
            .select("id, note_id, owner_id, storage_path, sort_order, width, height")
            .in_("note_id", [str(note_id) for note_id in note_ids])
            .order("sort_order", desc=False)
            .execute()
        )
        grouped: defaultdict[UUID, list[StoredTravelNoteImage]] = defaultdict(list)
        for row in _row_list(response.data):
            grouped[UUID(str(row["note_id"]))].append(_stored_image_from_row(row))
        return {note_id: tuple(images) for note_id, images in grouped.items()}

    def _load_profile(self, query_client, author_id: UUID) -> dict[str, Any] | None:
        response = (
            query_client.table("profiles")
            .select("display_name, avatar_path, creator_slug")
            .eq("user_id", str(author_id))
            .execute()
        )
        rows = _row_list(response.data)
        return rows[0] if rows else None

    def _load_profiles_by_author_ids(
        self, query_client, author_ids: set[UUID]
    ) -> dict[UUID, dict[str, Any]]:
        response = (
            query_client.table("profiles")
            .select("user_id, display_name, avatar_path, creator_slug")
            .in_("user_id", [str(author_id) for author_id in author_ids])
            .execute()
        )
        return {
            UUID(str(row["user_id"])): row
            for row in _row_list(response.data)
        }

    def _insert_images(
        self, user_id: UUID, note_id: UUID, images: list[TravelNoteImageInput]
    ) -> None:
        if not images:
            return
        self._insert_image_rows(
            [
                {
                    "note_id": str(note_id),
                    "owner_id": str(user_id),
                    "storage_path": image.storage_path,
                    "sort_order": image.sort_order,
                    "width": image.width,
                    "height": image.height,
                }
                for image in images
            ]
        )

    def _replace_images(
        self,
        user_id: UUID,
        note_id: UUID,
        images: list[TravelNoteImageInput],
        original_images: list[dict[str, Any]],
    ) -> None:
        try:
            self._delete_all_images(user_id, note_id)
            self._insert_images(user_id, note_id, images)
        except Exception as exc:
            try:
                self._restore_images(user_id, note_id, original_images)
            except Exception as compensation_exc:
                raise _compensation_failed(compensation_exc) from exc
            raise

    def _insert_image_rows(self, rows: list[dict[str, Any]]) -> None:
        if rows:
            self._client.table("travel_note_images").insert(rows).execute()

    def _delete_all_images(self, user_id: UUID, note_id: UUID) -> None:
        self._client.table("travel_note_images").delete().eq(
            "note_id", str(note_id)
        ).eq("owner_id", str(user_id)).execute()

    def _restore_images(
        self, user_id: UUID, note_id: UUID, original_images: list[dict[str, Any]]
    ) -> None:
        self._delete_all_images(user_id, note_id)
        self._insert_image_rows(deepcopy(original_images))

    def _cleanup_created_draft(self, user_id: UUID, note_id: UUID) -> None:
        errors: list[Exception] = []
        try:
            self._delete_all_images(user_id, note_id)
        except Exception as exc:
            errors.append(exc)
        try:
            self._client.table("travel_notes").delete().eq(
                "id", str(note_id)
            ).eq("author_id", str(user_id)).execute()
        except Exception as exc:
            errors.append(exc)
        if errors:
            raise _compensation_failed(errors[0])

    def _restore_draft(
        self,
        user_id: UUID,
        note_id: UUID,
        original_row: dict[str, Any],
        original_images: list[dict[str, Any]],
    ) -> None:
        errors: list[Exception] = []
        try:
            self._client.table("travel_notes").update(
                {
                    "title": original_row.get("title"),
                    "body": original_row.get("body"),
                    "location_name": original_row.get("location_name"),
                    "category": original_row.get("category"),
                    "source_trip_id": original_row.get("source_trip_id"),
                    "updated_at": original_row.get("updated_at"),
                }
            ).eq("id", str(note_id)).eq("author_id", str(user_id)).execute()
        except Exception as exc:
            errors.append(exc)
        try:
            self._restore_images(user_id, note_id, original_images)
        except Exception as exc:
            errors.append(exc)
        if errors:
            raise _compensation_failed(errors[0])

    def _reindex_images(self, user_id: UUID, note_id: UUID) -> None:
        images = self._load_images(self._client, note_id)
        for index, image in enumerate(images):
            if image.sort_order == index:
                continue
            self._client.table("travel_note_images").update({"sort_order": index}).eq(
                "id", str(image.id)
            ).eq("note_id", str(note_id)).eq("owner_id", str(user_id)).execute()


class SupabasePublicTravelNoteRepository:
    def __init__(self, client) -> None:
        self._client = client

    def list_public(
        self,
        cursor: tuple[datetime, UUID] | None,
        limit: int,
        *,
        category: str | None,
        search_query: str | None,
    ) -> list[StoredTravelNote]:
        params = dict(
            zip(
                _PUBLIC_LIST_RPC_PARAMS,
                (
                    cursor[0].isoformat() if cursor is not None else None,
                    str(cursor[1]) if cursor is not None else None,
                    limit,
                    category,
                    search_query,
                ),
                strict=True,
            )
        )
        try:
            with database_operation("travel_note.list_public"):
                response = self._client.rpc(
                    "list_public_travel_notes_internal", params
                ).execute()
            return [
                _stored_note_from_public_list_row(row) for row in _row_list(response.data)
            ]
        except AppError:
            raise
        except Exception as exc:  # pragma: no cover - exercised through fakes
            raise _map_travel_note_database_error(exc) from exc

    def get_public(self, note_id: UUID) -> StoredTravelNote | None:
        try:
            with database_operation(
                "travel_note.get_public", subject=hashed_log_subject("travel_note", note_id)
            ):
                response = self._client.rpc(
                    "get_public_travel_note_internal",
                    {_RPC_NOTE_ID_PARAM: str(note_id)},
                ).execute()
            rows = _row_list(response.data)
            if not rows:
                return None
            return _stored_note_from_public_detail_row(rows[0])
        except AppError:
            raise
        except Exception as exc:  # pragma: no cover - exercised through fakes
            raise _map_travel_note_database_error(exc) from exc

    def list_public_by_creator(
        self,
        creator_slug: str,
        cursor: tuple[datetime, UUID] | None,
        limit: int,
    ) -> list[StoredTravelNote]:
        params = {
            "p_creator_slug": creator_slug,
            "cursor_published_at": (
                cursor[0].isoformat() if cursor is not None else None
            ),
            "cursor_id": str(cursor[1]) if cursor is not None else None,
            "page_size": limit,
        }
        try:
            with database_operation(
                "travel_note.list_public_by_creator",
                subject=creator_slug,
            ):
                response = self._client.rpc(
                    "list_public_travel_notes_by_creator_internal",
                    params,
                ).execute()
            return [
                _stored_note_from_public_list_row(row) for row in _row_list(response.data)
            ]
        except AppError:
            raise
        except Exception as exc:  # pragma: no cover - exercised through fakes
            raise _map_travel_note_database_error(exc) from exc


class SupabaseTravelNoteMediaGateway:
    def __init__(
        self,
        client,
        *,
        bucket_name: str = COMMUNITY_MEDIA_BUCKET,
        expires_in: int = DEFAULT_SIGNED_URL_TTL_SECONDS,
    ) -> None:
        self._client = client
        self._bucket_name = bucket_name
        self._expires_in = expires_in

    def sign_paths(self, paths: list[str]) -> list[str]:
        if not paths:
            return []
        raw = self._client.storage.from_(self._bucket_name).create_signed_urls(
            paths, self._expires_in
        )
        rows = raw.get("data") if isinstance(raw, dict) else raw
        if not isinstance(rows, list):
            raise RuntimeError("travel note media signing returned an invalid payload")
        signed_urls: list[str] = []
        for row in rows:
            if isinstance(row, str):
                signed_urls.append(row)
                continue
            if not isinstance(row, dict):
                raise RuntimeError("travel note media signing returned an invalid row")
            signed_url = row.get("signedURL") or row.get("signedUrl") or row.get("signed_url")
            if not isinstance(signed_url, str) or not signed_url.strip():
                raise RuntimeError("travel note media signing returned no signed URL")
            signed_urls.append(signed_url)
        return signed_urls


def create_internal_supabase_client(url: str, service_key: str):
    from supabase import create_client

    return create_client(url, service_key)


def create_user_scoped_travel_note_repository(
    url: str,
    anon_key: str,
    access_token: str,
    *,
    internal_client=None,
) -> SupabaseTravelNoteRepository:
    from supabase import create_client

    client = create_client(url, anon_key)
    client.postgrest.auth(access_token)
    return SupabaseTravelNoteRepository(client, internal_client=internal_client)


def create_public_travel_note_repository(
    url: str, service_key: str, *, client=None
) -> SupabasePublicTravelNoteRepository:
    return SupabasePublicTravelNoteRepository(
        client if client is not None else create_internal_supabase_client(url, service_key)
    )


def create_travel_note_media_gateway(
    url: str, service_key: str, *, client=None
) -> SupabaseTravelNoteMediaGateway:
    return SupabaseTravelNoteMediaGateway(
        client if client is not None else create_internal_supabase_client(url, service_key)
    )


def _extract_note_id(data: object) -> UUID:
    rows = _row_list(data, allow_single_object=True)
    if not rows:
        raise RuntimeError("travel note operation returned no row")
    return UUID(str(rows[0]["id"]))


def _row_list(data: object, *, allow_single_object: bool = False) -> list[dict[str, Any]]:
    if allow_single_object and isinstance(data, dict):
        return [data]
    if not isinstance(data, list):
        raise RuntimeError("travel note operation returned an invalid row set")
    if not all(isinstance(row, dict) for row in data):
        raise RuntimeError("travel note operation returned an invalid row set")
    return data


def _stored_note_from_row(
    row: dict[str, Any],
    *,
    images: tuple[StoredTravelNoteImage, ...],
    profile: dict[str, Any] | None,
) -> StoredTravelNote:
    author_id = UUID(str(row["author_id"]))
    created_at = _parse_datetime(row.get("created_at")) or datetime.now(UTC)
    updated_at = _parse_datetime(row.get("updated_at")) or created_at
    author_display_name = _DEFAULT_AUTHOR_DISPLAY_NAME
    author_avatar_path = None
    author_slug = _DEFAULT_AUTHOR_SLUG
    if profile is not None:
        display_name = profile.get("display_name")
        if isinstance(display_name, str) and display_name.strip():
            author_display_name = display_name.strip()[:40]
        avatar_path = profile.get("avatar_path")
        if isinstance(avatar_path, str) and avatar_path.strip():
            author_avatar_path = avatar_path.strip()
        creator_slug = profile.get("creator_slug")
        if isinstance(creator_slug, str) and creator_slug.strip():
            author_slug = creator_slug.strip()
    return StoredTravelNote(
        id=UUID(str(row["id"])),
        author_id=author_id,
        title=str(row["title"]).strip(),
        body=str(row["body"]).strip(),
        location_name=str(row["location_name"]).strip(),
        category=str(row["category"]).strip(),
        status=str(row["status"]).strip(),
        review_reason=_optional_trimmed_text(row.get("review_reason")),
        source_trip_id=_optional_uuid(row.get("source_trip_id")),
        itinerary_snapshot=_optional_dict(row.get("itinerary_snapshot")),
        created_at=created_at,
        updated_at=updated_at,
        submitted_at=_parse_datetime(row.get("submitted_at")),
        published_at=_parse_datetime(row.get("published_at")),
        deleted_at=_parse_datetime(row.get("deleted_at")),
        like_count=int(row.get("like_count", 0)),
        comment_count=int(row.get("comment_count", 0)),
        author_display_name=author_display_name,
        author_avatar_path=author_avatar_path,
        author_slug=author_slug,
        images=images,
    )


def _stored_note_from_public_list_row(row: dict[str, Any]) -> StoredTravelNote:
    note_id = UUID(str(row["id"]))
    published_at = _parse_datetime(row.get("published_at")) or datetime.now(UTC)
    author_slug = _trim_or_default(row.get("creator_slug"), _DEFAULT_AUTHOR_SLUG)
    cover_path = _trim_or_default(row.get("cover_storage_path"), "")
    if not cover_path:
        raise RuntimeError("public travel note list row is missing a cover path")
    return StoredTravelNote(
        id=note_id,
        author_id=uuid5(NAMESPACE_URL, f"travel-note-author:{author_slug}"),
        title=_trim_or_default(row.get("title"), ""),
        body=_trim_or_default(row.get("body"), _trim_or_default(row.get("title"), "")),
        location_name=_trim_or_default(row.get("location_name"), ""),
        category=_trim_or_default(row.get("category"), ""),
        status="approved",
        review_reason=None,
        source_trip_id=None,
        itinerary_snapshot=None,
        created_at=published_at,
        updated_at=published_at,
        submitted_at=published_at,
        published_at=published_at,
        deleted_at=None,
        like_count=int(row.get("like_count", 0)),
        comment_count=int(row.get("comment_count", 0)),
        author_display_name=_trim_or_default(
            row.get("author_display_name"), _DEFAULT_AUTHOR_DISPLAY_NAME
        ),
        author_avatar_path=_optional_trimmed_text(row.get("author_avatar_path")),
        author_slug=author_slug,
        images=(
            StoredTravelNoteImage(
                id=uuid5(
                    NAMESPACE_URL, f"travel-note-public-image:{note_id}:0:{cover_path}"
                ),
                storage_path=cover_path,
                sort_order=0,
                width=1,
                height=1,
            ),
        ),
    )


def _stored_note_from_public_detail_row(row: dict[str, Any]) -> StoredTravelNote:
    note_id = UUID(str(row["id"]))
    author_slug = _trim_or_default(row.get("creator_slug"), _DEFAULT_AUTHOR_SLUG)
    published_at = _parse_datetime(row.get("published_at")) or datetime.now(UTC)
    manifest = row.get("image_manifest")
    if not isinstance(manifest, list):
        raise RuntimeError("public travel note detail returned an invalid image manifest")
    images = tuple(
        StoredTravelNoteImage(
            id=uuid5(
                NAMESPACE_URL,
                f"travel-note-public-image:{note_id}:{item.get('sort_order')}:{item.get('storage_path')}",
            ),
            storage_path=_trim_or_default(item.get("storage_path"), ""),
            sort_order=int(item.get("sort_order", 0)),
            width=int(item.get("width", 1)),
            height=int(item.get("height", 1)),
        )
        for item in manifest
        if isinstance(item, dict)
    )
    if not images:
        raise RuntimeError("public travel note detail returned no images")
    return StoredTravelNote(
        id=note_id,
        author_id=uuid5(NAMESPACE_URL, f"travel-note-author:{author_slug}"),
        title=_trim_or_default(row.get("title"), ""),
        body=_trim_or_default(row.get("body"), ""),
        location_name=_trim_or_default(row.get("location_name"), ""),
        category=_trim_or_default(row.get("category"), ""),
        status="approved",
        review_reason=None,
        source_trip_id=None,
        itinerary_snapshot=_optional_dict(row.get("itinerary_snapshot")),
        created_at=published_at,
        updated_at=published_at,
        submitted_at=published_at,
        published_at=published_at,
        deleted_at=None,
        like_count=int(row.get("like_count", 0)),
        comment_count=int(row.get("comment_count", 0)),
        author_display_name=_trim_or_default(
            row.get("author_display_name"), _DEFAULT_AUTHOR_DISPLAY_NAME
        ),
        author_avatar_path=_optional_trimmed_text(row.get("author_avatar_path")),
        author_slug=author_slug,
        images=images,
    )


def _stored_image_from_row(row: dict[str, Any]) -> StoredTravelNoteImage:
    return StoredTravelNoteImage(
        id=UUID(str(row["id"])),
        storage_path=_trim_or_default(row.get("storage_path"), ""),
        sort_order=int(row.get("sort_order", 0)),
        width=int(row.get("width", 1)),
        height=int(row.get("height", 1)),
    )


def _parse_datetime(value: object) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        return datetime.fromisoformat(value)
    raise RuntimeError("travel note row contains an invalid timestamp")


def _optional_uuid(value: object) -> UUID | None:
    if value is None:
        return None
    return UUID(str(value))


def _optional_dict(value: object) -> dict[str, object] | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise RuntimeError("travel note row contains an invalid JSON object")
    return deepcopy(value)


def _optional_trimmed_text(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise RuntimeError("travel note row contains an invalid text value")
    normalized = value.strip()
    return normalized or None


def _trim_or_default(value: object, default: str) -> str:
    if not isinstance(value, str):
        return default
    normalized = value.strip()
    return normalized or default


def _map_travel_note_database_error(error: Exception) -> AppError:
    if isinstance(error, AppError):
        return error
    code = str(getattr(error, "code", "") or "").upper()
    message = str(error).lower()
    if code == "P0002" or "not found" in message:
        return AppError("TRAVEL_NOTE_NOT_FOUND", "Travel note not found")
    if code in {"23505", "23514", "22P02"}:
        return AppError(
            "TRAVEL_NOTE_VALIDATION_FAILED", "Travel note request validation failed"
        )
    if code == "P0001":
        if any(
            phrase in message
            for phrase in (
                "not submittable",
                "invalid state",
                "review is stale",
                "require rpc moderation flow",
            )
        ):
            return AppError(
                "TRAVEL_NOTE_INVALID_STATE", "Travel note state is invalid"
            )
        return AppError(
            "TRAVEL_NOTE_VALIDATION_FAILED", "Travel note request validation failed"
        )
    return AppError("TRAVEL_NOTE_UNAVAILABLE", "Travel note service is unavailable")


def _compensation_failed(error: Exception) -> AppError:
    return AppError(
        "TRAVEL_NOTE_UNAVAILABLE",
        "Travel note service is unavailable while restoring draft state",
    )
