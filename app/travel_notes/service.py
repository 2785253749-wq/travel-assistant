from __future__ import annotations

from datetime import UTC, datetime
from typing import cast, get_args
from uuid import UUID

from app.core.errors import AppError
from app.travel_notes.models import (
    TravelNoteCard,
    TravelNoteCategory,
    TravelNoteDetail,
    TravelNoteDraftInput,
    TravelNoteOwnerImage,
    TravelNoteOwnerView,
    TravelNotePage,
    TravelNotePublicImage,
    decode_travel_note_cursor,
    encode_travel_note_cursor,
)
from app.travel_notes.ports import (
    Clock,
    PublicTravelNoteRepository,
    StoredTravelNote,
    TravelNoteMediaGateway,
    TravelNoteRepository,
)


_VALID_CATEGORIES = set(cast(tuple[str, ...], get_args(TravelNoteCategory)))


class _SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


class _IdentityTravelNoteMediaGateway:
    def sign_paths(self, paths: list[str]) -> list[str]:
        return list(paths)


class TravelNoteModule:
    def __init__(
        self,
        repository: TravelNoteRepository,
        *,
        public_repository: PublicTravelNoteRepository | None = None,
        media_gateway: TravelNoteMediaGateway | None = None,
        clock: Clock | None = None,
    ) -> None:
        self._repository = repository
        self._public_repository = public_repository or repository
        self._media_gateway = media_gateway or _IdentityTravelNoteMediaGateway()
        self._clock = clock or _SystemClock()

    def create_draft(self, user_id: UUID, value: TravelNoteDraftInput) -> TravelNoteOwnerView:
        self._validate_owner_paths(user_id, value)
        itinerary_snapshot = self._itinerary_snapshot_for(user_id, value.source_trip_id)
        stored = self._repository.create_draft(
            user_id,
            value,
            now=self._clock.now(),
            itinerary_snapshot=itinerary_snapshot,
        )
        return self._to_owner_view(stored)

    def replace_draft(
        self, user_id: UUID, note_id: UUID, value: TravelNoteDraftInput
    ) -> TravelNoteOwnerView:
        current = self._repository.get_owned(user_id, note_id)
        if current is None:
            raise _not_found()
        if current.status not in {"draft", "rejected"}:
            raise _invalid_state()

        self._validate_owner_paths(user_id, value)
        itinerary_snapshot = self._itinerary_snapshot_for(user_id, value.source_trip_id)
        stored = self._repository.replace_draft(
            user_id,
            note_id,
            value,
            now=self._clock.now(),
            itinerary_snapshot=itinerary_snapshot,
        )
        if stored is None:
            raise _not_found()
        return self._to_owner_view(stored)

    def submit(self, user_id: UUID, note_id: UUID) -> TravelNoteOwnerView:
        current = self._repository.get_owned(user_id, note_id)
        if current is None:
            raise _not_found()
        if current.status not in {"draft", "rejected"}:
            raise _invalid_state()

        stored = self._repository.submit(user_id, note_id, now=self._clock.now())
        if stored is None:
            raise _not_found()
        return self._to_owner_view(stored)

    def soft_delete(self, user_id: UUID, note_id: UUID) -> None:
        if not self._repository.soft_delete(user_id, note_id, now=self._clock.now()):
            raise _not_found()

    def list_mine(self, user_id: UUID) -> list[TravelNoteOwnerView]:
        return [self._to_owner_view(note) for note in self._repository.list_owned(user_id)]

    def list_public(
        self,
        *,
        cursor: str | None,
        limit: int,
        category: TravelNoteCategory | None = None,
        search_query: str | None = None,
    ) -> TravelNotePage:
        if not 1 <= limit <= 50:
            raise _validation_failed()
        if category is not None and category not in _VALID_CATEGORIES:
            raise _validation_failed()
        normalized_search = self._normalize_search(search_query)
        try:
            decoded_cursor = decode_travel_note_cursor(cursor) if cursor is not None else None
        except ValueError as exc:
            raise _validation_failed() from exc

        rows = self._public_repository.list_public(
            decoded_cursor,
            limit + 1,
            category=category,
            search_query=normalized_search,
        )
        visible = rows[:limit]
        next_cursor = None
        if len(rows) > limit and visible and visible[-1].published_at is not None:
            last_visible = visible[-1]
            next_cursor = encode_travel_note_cursor(last_visible.published_at, last_visible.id)
        return TravelNotePage(
            items=[self._to_card(row) for row in visible],
            next_cursor=next_cursor,
        )

    def get_public(self, note_id: UUID) -> TravelNoteDetail:
        stored = self._public_repository.get_public(note_id)
        if stored is None:
            raise _not_found()
        return self._to_detail(stored)

    def approve(self, reviewer_id: UUID, note_id: UUID) -> TravelNoteOwnerView:
        current = self._repository.get_note(note_id)
        if current is None or current.deleted_at is not None:
            raise _not_found()
        if current.status != "pending_review":
            raise _invalid_state()

        stored = self._repository.approve(reviewer_id, note_id, now=self._clock.now())
        if stored is None:
            raise _not_found()
        return self._to_owner_view(stored)

    def reject(self, reviewer_id: UUID, note_id: UUID, reason: str) -> TravelNoteOwnerView:
        current = self._repository.get_note(note_id)
        if current is None or current.deleted_at is not None:
            raise _not_found()
        if current.status != "pending_review":
            raise _invalid_state()

        normalized_reason = reason.strip()
        if not 1 <= len(normalized_reason) <= 500:
            raise _validation_failed()

        stored = self._repository.reject(
            reviewer_id,
            note_id,
            reason=normalized_reason,
            now=self._clock.now(),
        )
        if stored is None:
            raise _not_found()
        return self._to_owner_view(stored)

    def _validate_owner_paths(self, user_id: UUID, value: TravelNoteDraftInput) -> None:
        prefix = f"{user_id}/"
        if any(not image.storage_path.startswith(prefix) for image in value.images):
            raise _validation_failed()

    def _itinerary_snapshot_for(
        self, user_id: UUID, source_trip_id: UUID | None
    ) -> dict[str, object] | None:
        if source_trip_id is None:
            return None
        snapshot = self._repository.get_source_trip_snapshot(user_id, source_trip_id)
        if snapshot is None:
            raise _not_found()
        return snapshot

    @staticmethod
    def _normalize_search(value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip().lower()
        return normalized or None

    def _to_owner_view(self, stored: StoredTravelNote) -> TravelNoteOwnerView:
        avatar_url = self._signed_optional_path(stored.author_avatar_path)
        cover_image_url = self._signed_cover_path(stored)
        return TravelNoteOwnerView(
            id=stored.id,
            title=stored.title,
            body=stored.body,
            location_name=stored.location_name,
            category=stored.category,
            status=stored.status,
            review_reason=stored.review_reason,
            source_trip_id=stored.source_trip_id,
            submitted_at=stored.submitted_at,
            published_at=stored.published_at,
            updated_at=stored.updated_at,
            deleted_at=stored.deleted_at,
            cover_image_url=cover_image_url,
            author_display_name=stored.author_display_name,
            author_avatar_url=avatar_url,
            like_count=stored.like_count,
            comment_count=stored.comment_count,
            images=[
                TravelNoteOwnerImage(
                    id=image.id,
                    storage_path=image.storage_path,
                    sort_order=image.sort_order,
                    width=image.width,
                    height=image.height,
                )
                for image in sorted(stored.images, key=lambda item: item.sort_order)
            ],
        )

    def _to_card(self, stored: StoredTravelNote) -> TravelNoteCard:
        published_at = stored.published_at
        if stored.status != "approved" or stored.deleted_at is not None or published_at is None:
            raise _not_found()
        return TravelNoteCard(
            id=stored.id,
            title=stored.title,
            body_preview=self._body_preview(stored.body),
            location_name=stored.location_name,
            category=stored.category,
            cover_image_url=self._signed_cover_path(stored),
            author_display_name=stored.author_display_name,
            author_avatar_url=self._signed_optional_path(stored.author_avatar_path),
            published_at=published_at,
            like_count=stored.like_count,
            comment_count=stored.comment_count,
        )

    def _to_detail(self, stored: StoredTravelNote) -> TravelNoteDetail:
        published_at = stored.published_at
        if stored.status != "approved" or stored.deleted_at is not None or published_at is None:
            raise _not_found()

        ordered_images = sorted(stored.images, key=lambda item: item.sort_order)
        signed_paths = self._media_gateway.sign_paths(
            [image.storage_path for image in ordered_images]
        )
        return TravelNoteDetail(
            id=stored.id,
            title=stored.title,
            body=stored.body,
            location_name=stored.location_name,
            category=stored.category,
            cover_image_url=signed_paths[0],
            author_display_name=stored.author_display_name,
            author_avatar_url=self._signed_optional_path(stored.author_avatar_path),
            author_slug=stored.author_slug,
            published_at=published_at,
            like_count=stored.like_count,
            comment_count=stored.comment_count,
            images=[
                TravelNotePublicImage(
                    id=image.id,
                    image_url=image_url,
                    sort_order=image.sort_order,
                    width=image.width,
                    height=image.height,
                )
                for image, image_url in zip(ordered_images, signed_paths, strict=True)
            ],
        )

    def _signed_cover_path(self, stored: StoredTravelNote) -> str:
        ordered_images = sorted(stored.images, key=lambda item: item.sort_order)
        return self._media_gateway.sign_paths([ordered_images[0].storage_path])[0]

    def _signed_optional_path(self, path: str | None) -> str | None:
        if path is None:
            return None
        return self._media_gateway.sign_paths([path])[0]

    @staticmethod
    def _body_preview(body: str) -> str:
        normalized = body.strip()
        if len(normalized) <= 500:
            return normalized
        return normalized[:497].rstrip() + "..."


def _not_found() -> AppError:
    return AppError("TRAVEL_NOTE_NOT_FOUND", "Travel note not found")


def _invalid_state() -> AppError:
    return AppError("TRAVEL_NOTE_INVALID_STATE", "Travel note state is invalid")


def _validation_failed() -> AppError:
    return AppError(
        "TRAVEL_NOTE_VALIDATION_FAILED", "Travel note request validation failed"
    )

