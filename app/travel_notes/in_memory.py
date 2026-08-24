from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID, uuid4

from app.travel_notes.models import TravelNoteDraftInput, TravelNoteImageInput
from app.travel_notes.ports import StoredTravelNote, StoredTravelNoteImage


_DEFAULT_AUTHOR_DISPLAY_NAME = "Voyage 旅行者"
_DEFAULT_AUTHOR_SLUG = "voyage-traveler"


class FixedClock:
    def __init__(self, value: datetime | None = None) -> None:
        self._value = value or datetime.now(UTC)

    def now(self) -> datetime:
        return self._value

    def set(self, value: datetime) -> None:
        self._value = value


class InMemoryTravelNoteMediaGateway:
    def sign_paths(self, paths: list[str]) -> list[str]:
        return [f"https://signed.example.test/{path}" for path in paths]


class InMemoryTravelNoteRepository:
    def __init__(self) -> None:
        self._notes: dict[UUID, StoredTravelNote] = {}
        self._source_trips: dict[tuple[UUID, UUID], dict[str, object]] = {}

    def add_source_trip(
        self,
        user_id: UUID,
        trip_id: UUID,
        itinerary_snapshot: dict[str, object],
    ) -> None:
        self._source_trips[(user_id, trip_id)] = deepcopy(itinerary_snapshot)

    def create_draft(
        self,
        user_id: UUID,
        value: TravelNoteDraftInput,
        *,
        now: datetime,
        itinerary_snapshot: dict[str, object] | None,
    ) -> StoredTravelNote:
        note = StoredTravelNote(
            id=uuid4(),
            author_id=user_id,
            title=value.title,
            body=value.body,
            location_name=value.location_name,
            category=value.category,
            status="draft",
            review_reason=None,
            source_trip_id=value.source_trip_id,
            itinerary_snapshot=deepcopy(itinerary_snapshot),
            created_at=now,
            updated_at=now,
            submitted_at=None,
            published_at=None,
            deleted_at=None,
            like_count=0,
            comment_count=0,
            author_display_name=_DEFAULT_AUTHOR_DISPLAY_NAME,
            author_avatar_path=None,
            author_slug=_DEFAULT_AUTHOR_SLUG,
            images=self._images_from_input(value),
        )
        self._notes[note.id] = note
        return self.get_note(note.id)  # type: ignore[return-value]

    def replace_draft(
        self,
        user_id: UUID,
        note_id: UUID,
        value: TravelNoteDraftInput,
        *,
        now: datetime,
        itinerary_snapshot: dict[str, object] | None,
    ) -> StoredTravelNote | None:
        stored = self._notes.get(note_id)
        if stored is None or stored.author_id != user_id or stored.deleted_at is not None:
            return None
        updated = replace(
            stored,
            title=value.title,
            body=value.body,
            location_name=value.location_name,
            category=value.category,
            source_trip_id=value.source_trip_id,
            itinerary_snapshot=deepcopy(itinerary_snapshot),
            updated_at=now,
            images=self._images_from_input(value),
        )
        self._notes[note_id] = updated
        return self.get_note(note_id)

    def attach_image(
        self,
        user_id: UUID,
        note_id: UUID,
        image: TravelNoteImageInput,
        *,
        now: datetime,
    ) -> StoredTravelNote | None:
        stored = self._notes.get(note_id)
        if stored is None or stored.author_id != user_id or stored.deleted_at is not None:
            return None
        next_images = list(stored.images)
        next_images.append(
            StoredTravelNoteImage(
                id=uuid4(),
                storage_path=image.storage_path,
                sort_order=image.sort_order,
                width=image.width,
                height=image.height,
            )
        )
        self._notes[note_id] = replace(
            stored,
            images=tuple(next_images),
            updated_at=now,
        )
        return self.get_note(note_id)

    def remove_image(
        self,
        user_id: UUID,
        note_id: UUID,
        image_id: UUID,
        *,
        now: datetime,
    ) -> StoredTravelNote | None:
        stored = self._notes.get(note_id)
        if stored is None or stored.author_id != user_id or stored.deleted_at is not None:
            return None
        remaining = [image for image in stored.images if image.id != image_id]
        if len(remaining) == len(stored.images):
            return None
        reindexed = tuple(
            replace(image, sort_order=index) for index, image in enumerate(remaining)
        )
        self._notes[note_id] = replace(
            stored,
            images=reindexed,
            updated_at=now,
        )
        return self.get_note(note_id)

    def get_owned(self, user_id: UUID, note_id: UUID) -> StoredTravelNote | None:
        stored = self._notes.get(note_id)
        if stored is None or stored.author_id != user_id or stored.deleted_at is not None:
            return None
        return deepcopy(stored)

    def get_note(self, note_id: UUID) -> StoredTravelNote | None:
        stored = self._notes.get(note_id)
        return None if stored is None else deepcopy(stored)

    def submit(self, user_id: UUID, note_id: UUID, *, now: datetime) -> StoredTravelNote | None:
        stored = self._notes.get(note_id)
        if stored is None or stored.author_id != user_id or stored.deleted_at is not None:
            return None
        updated = replace(
            stored,
            status="pending_review",
            review_reason=None,
            submitted_at=now,
            published_at=None,
            updated_at=now,
        )
        self._notes[note_id] = updated
        return self.get_note(note_id)

    def soft_delete(self, user_id: UUID, note_id: UUID, *, now: datetime) -> bool:
        stored = self._notes.get(note_id)
        if stored is None or stored.author_id != user_id or stored.deleted_at is not None:
            return False
        self._notes[note_id] = replace(stored, deleted_at=now, updated_at=now)
        return True

    def list_owned(self, user_id: UUID) -> list[StoredTravelNote]:
        rows = [
            deepcopy(stored)
            for stored in self._notes.values()
            if stored.author_id == user_id and stored.deleted_at is None
        ]
        rows.sort(key=lambda note: (note.updated_at, str(note.id)), reverse=True)
        return rows

    def get_source_trip_snapshot(
        self, user_id: UUID, trip_id: UUID
    ) -> dict[str, object] | None:
        snapshot = self._source_trips.get((user_id, trip_id))
        return None if snapshot is None else deepcopy(snapshot)

    def approve(self, reviewer_id: UUID, note_id: UUID, *, now: datetime) -> StoredTravelNote | None:
        del reviewer_id
        stored = self._notes.get(note_id)
        if stored is None or stored.deleted_at is not None:
            return None
        updated = replace(
            stored,
            status="approved",
            review_reason=None,
            published_at=now,
            updated_at=now,
        )
        self._notes[note_id] = updated
        return self.get_note(note_id)

    def reject(
        self,
        reviewer_id: UUID,
        note_id: UUID,
        *,
        reason: str,
        now: datetime,
    ) -> StoredTravelNote | None:
        del reviewer_id
        stored = self._notes.get(note_id)
        if stored is None or stored.deleted_at is not None:
            return None
        updated = replace(
            stored,
            status="rejected",
            review_reason=reason,
            published_at=None,
            updated_at=now,
        )
        self._notes[note_id] = updated
        return self.get_note(note_id)

    def list_public(
        self,
        cursor: tuple[datetime, UUID] | None,
        limit: int,
        *,
        category: str | None,
        search_query: str | None,
    ) -> list[StoredTravelNote]:
        rows = [
            deepcopy(stored)
            for stored in self._notes.values()
            if stored.status == "approved"
            and stored.deleted_at is None
            and stored.published_at is not None
        ]
        if category is not None:
            rows = [row for row in rows if row.category == category]
        if search_query is not None:
            normalized_query = search_query.strip().lower()
            rows = [
                row
                for row in rows
                if normalized_query in row.title.lower()
                or normalized_query in row.location_name.lower()
            ]
        rows.sort(key=lambda note: (note.published_at, str(note.id)), reverse=True)
        if cursor is None:
            return rows[:limit]

        cursor_published_at, cursor_id = cursor
        filtered = [
            row
            for row in rows
            if row.published_at < cursor_published_at
            or (row.published_at == cursor_published_at and str(row.id) < str(cursor_id))
        ]
        return filtered[:limit]

    def get_public(self, note_id: UUID) -> StoredTravelNote | None:
        stored = self._notes.get(note_id)
        if (
            stored is None
            or stored.deleted_at is not None
            or stored.status != "approved"
            or stored.published_at is None
        ):
            return None
        return deepcopy(stored)

    def get_stored_note(self, note_id: UUID) -> StoredTravelNote | None:
        return self.get_note(note_id)

    @staticmethod
    def _images_from_input(value: TravelNoteDraftInput) -> tuple[StoredTravelNoteImage, ...]:
        return tuple(
            StoredTravelNoteImage(
                id=uuid4(),
                storage_path=image.storage_path,
                sort_order=image.sort_order,
                width=image.width,
                height=image.height,
            )
            for image in value.images
        )
