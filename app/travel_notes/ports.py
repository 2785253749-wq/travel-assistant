from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from app.travel_notes.models import (
    TravelNoteCategory,
    TravelNoteDraftInput,
    TravelNoteImageInput,
    TravelNoteStatus,
)


@dataclass(frozen=True, slots=True)
class StoredTravelNoteImage:
    id: UUID
    storage_path: str
    sort_order: int
    width: int
    height: int


@dataclass(frozen=True, slots=True)
class StoredTravelNote:
    id: UUID
    author_id: UUID
    title: str
    body: str
    location_name: str
    category: TravelNoteCategory
    status: TravelNoteStatus
    review_reason: str | None
    source_trip_id: UUID | None
    itinerary_snapshot: dict[str, object] | None
    created_at: datetime
    updated_at: datetime
    submitted_at: datetime | None
    published_at: datetime | None
    deleted_at: datetime | None
    like_count: int
    comment_count: int
    author_display_name: str
    author_avatar_path: str | None
    author_slug: str
    images: tuple[StoredTravelNoteImage, ...]


class Clock(Protocol):
    def now(self) -> datetime: ...


class TravelNoteMediaGateway(Protocol):
    def sign_paths(self, paths: list[str]) -> list[str]: ...


class TravelNoteRepository(Protocol):
    def create_draft(
        self,
        user_id: UUID,
        value: TravelNoteDraftInput,
        *,
        now: datetime,
        itinerary_snapshot: dict[str, object] | None,
    ) -> StoredTravelNote: ...

    def replace_draft(
        self,
        user_id: UUID,
        note_id: UUID,
        value: TravelNoteDraftInput,
        *,
        now: datetime,
        itinerary_snapshot: dict[str, object] | None,
    ) -> StoredTravelNote | None: ...

    def attach_image(
        self,
        user_id: UUID,
        note_id: UUID,
        image: TravelNoteImageInput,
        *,
        now: datetime,
    ) -> StoredTravelNote | None: ...

    def remove_image(
        self,
        user_id: UUID,
        note_id: UUID,
        image_id: UUID,
        *,
        now: datetime,
    ) -> StoredTravelNote | None: ...

    def get_owned(self, user_id: UUID, note_id: UUID) -> StoredTravelNote | None: ...
    def get_note(self, note_id: UUID) -> StoredTravelNote | None: ...

    def submit(self, user_id: UUID, note_id: UUID, *, now: datetime) -> StoredTravelNote | None: ...
    def soft_delete(self, user_id: UUID, note_id: UUID, *, now: datetime) -> bool: ...
    def list_owned(self, user_id: UUID) -> list[StoredTravelNote]: ...

    def get_source_trip_snapshot(
        self, user_id: UUID, trip_id: UUID
    ) -> dict[str, object] | None: ...

    def approve(self, reviewer_id: UUID, note_id: UUID, *, now: datetime) -> StoredTravelNote | None: ...
    def reject(
        self,
        reviewer_id: UUID,
        note_id: UUID,
        *,
        reason: str,
        now: datetime,
    ) -> StoredTravelNote | None: ...


class PublicTravelNoteRepository(Protocol):
    def list_public(
        self,
        cursor: tuple[datetime, UUID] | None,
        limit: int,
        *,
        category: TravelNoteCategory | None,
        search_query: str | None,
    ) -> list[StoredTravelNote]: ...

    def get_public(self, note_id: UUID) -> StoredTravelNote | None: ...
