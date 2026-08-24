from __future__ import annotations

from base64 import urlsafe_b64decode, urlsafe_b64encode
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from app.schemas import StrictSchema

TravelNoteCategory = Literal["摄影控", "美食地图", "独自旅行", "城市漫步", "自然风光", "亲子游"]
TravelNoteStatus = Literal["draft", "pending_review", "approved", "rejected"]
ReviewTargetType = Literal["note", "comment"]
TravelNoteCommentStatus = Literal["pending_review", "approved", "rejected"]


def _trim_text(value: object) -> object:
    if isinstance(value, str):
        return value.strip()
    return value


class TravelNoteImageInput(StrictSchema):
    storage_path: str = Field(min_length=5, max_length=500)
    sort_order: int = Field(ge=0, le=8)
    width: int = Field(ge=1, le=8192)
    height: int = Field(ge=1, le=8192)

    @field_validator("storage_path", mode="before")
    @classmethod
    def _trim_storage_path(cls, value: object) -> object:
        return _trim_text(value)


class TravelNoteDraftInput(StrictSchema):
    title: str = Field(min_length=1, max_length=60)
    body: str = Field(min_length=1, max_length=5000)
    location_name: str = Field(min_length=1, max_length=80)
    category: TravelNoteCategory
    source_trip_id: UUID | None = None
    images: list[TravelNoteImageInput] = Field(min_length=1, max_length=9)

    @field_validator("title", "body", "location_name", mode="before")
    @classmethod
    def _trim_text_fields(cls, value: object) -> object:
        return _trim_text(value)

    @model_validator(mode="after")
    def _images_have_unique_sort_orders(self) -> "TravelNoteDraftInput":
        sort_orders = [image.sort_order for image in self.images]
        if len(sort_orders) != len(set(sort_orders)):
            raise ValueError("image sort_order values must be unique")
        if sorted(sort_orders) != list(range(len(sort_orders))):
            raise ValueError("image sort_order values must start at zero and be contiguous")
        return self


class TravelNoteCard(StrictSchema):
    id: UUID
    title: str = Field(min_length=1, max_length=60)
    body_preview: str = Field(min_length=1, max_length=500)
    location_name: str = Field(min_length=1, max_length=80)
    category: TravelNoteCategory
    cover_image_url: str = Field(min_length=1, max_length=2048)
    author_display_name: str = Field(min_length=1, max_length=40)
    author_avatar_url: str | None = Field(default=None, min_length=1, max_length=2048)
    published_at: datetime
    like_count: int = Field(ge=0)
    comment_count: int = Field(ge=0)

    @field_validator(
        "title",
        "body_preview",
        "location_name",
        "cover_image_url",
        "author_display_name",
        "author_avatar_url",
        mode="before",
    )
    @classmethod
    def _trim_card_text(cls, value: object) -> object:
        return _trim_text(value)


class TravelNotePublicImage(StrictSchema):
    id: UUID
    image_url: str = Field(min_length=1, max_length=2048)
    sort_order: int = Field(ge=0, le=8)
    width: int = Field(ge=1, le=8192)
    height: int = Field(ge=1, le=8192)

    @field_validator("image_url", mode="before")
    @classmethod
    def _trim_image_url(cls, value: object) -> object:
        return _trim_text(value)


class TravelNoteOwnerImage(StrictSchema):
    id: UUID
    storage_path: str = Field(min_length=5, max_length=500)
    sort_order: int = Field(ge=0, le=8)
    width: int = Field(ge=1, le=8192)
    height: int = Field(ge=1, le=8192)

    @field_validator("storage_path", mode="before")
    @classmethod
    def _trim_owner_storage_path(cls, value: object) -> object:
        return _trim_text(value)


class TravelNoteDetail(StrictSchema):
    id: UUID
    title: str = Field(min_length=1, max_length=60)
    body: str = Field(min_length=1, max_length=5000)
    location_name: str = Field(min_length=1, max_length=80)
    category: TravelNoteCategory
    cover_image_url: str = Field(min_length=1, max_length=2048)
    author_display_name: str = Field(min_length=1, max_length=40)
    author_avatar_url: str | None = Field(default=None, min_length=1, max_length=2048)
    author_slug: str = Field(min_length=1, max_length=100)
    published_at: datetime
    like_count: int = Field(ge=0)
    comment_count: int = Field(ge=0)
    images: list[TravelNotePublicImage] = Field(min_length=1, max_length=9)

    @field_validator(
        "title",
        "body",
        "location_name",
        "cover_image_url",
        "author_display_name",
        "author_avatar_url",
        "author_slug",
        mode="before",
    )
    @classmethod
    def _trim_detail_text(cls, value: object) -> object:
        return _trim_text(value)

    @model_validator(mode="after")
    def _images_are_contiguous(self) -> "TravelNoteDetail":
        sort_orders = [image.sort_order for image in self.images]
        if sorted(sort_orders) != list(range(len(sort_orders))):
            raise ValueError("image sort_order values must start at zero and be contiguous")
        return self


class TravelNoteOwnerView(StrictSchema):
    id: UUID
    title: str = Field(min_length=1, max_length=60)
    body: str = Field(min_length=1, max_length=5000)
    location_name: str = Field(min_length=1, max_length=80)
    category: TravelNoteCategory
    status: TravelNoteStatus
    review_reason: str | None = Field(default=None, min_length=1, max_length=500)
    source_trip_id: UUID | None = None
    submitted_at: datetime | None = None
    published_at: datetime | None = None
    updated_at: datetime
    deleted_at: datetime | None = None
    cover_image_url: str = Field(min_length=1, max_length=2048)
    author_display_name: str = Field(min_length=1, max_length=40)
    author_avatar_url: str | None = Field(default=None, min_length=1, max_length=2048)
    like_count: int = Field(ge=0)
    comment_count: int = Field(ge=0)
    images: list[TravelNoteOwnerImage] = Field(min_length=1, max_length=9)

    @field_validator(
        "title",
        "body",
        "location_name",
        "review_reason",
        "cover_image_url",
        "author_display_name",
        "author_avatar_url",
        mode="before",
    )
    @classmethod
    def _trim_owner_text(cls, value: object) -> object:
        return _trim_text(value)

    @model_validator(mode="after")
    def _validate_lifecycle_shape(self) -> "TravelNoteOwnerView":
        sort_orders = [image.sort_order for image in self.images]
        if sorted(sort_orders) != list(range(len(sort_orders))):
            raise ValueError("image sort_order values must start at zero and be contiguous")

        if self.status == "draft":
            if self.submitted_at is not None or self.published_at is not None or self.review_reason is not None:
                raise ValueError("draft notes must not carry submission, publication, or review state")
        elif self.status == "pending_review":
            if self.submitted_at is None or self.published_at is not None or self.review_reason is not None:
                raise ValueError("pending review notes must have a submission timestamp and no review outcome")
        elif self.status == "approved":
            if self.submitted_at is None or self.published_at is None or self.review_reason is not None:
                raise ValueError("approved notes must have submission and publication timestamps and no review reason")
        elif self.status == "rejected":
            if self.submitted_at is None or self.published_at is not None or self.review_reason is None:
                raise ValueError("rejected notes must have a submission timestamp and review reason")
        return self


class TravelNotePage(StrictSchema):
    items: list[TravelNoteCard]
    next_cursor: str | None = None

class TravelNoteCreator(StrictSchema):
    creator_slug: str = Field(min_length=1, max_length=100)
    display_name: str = Field(min_length=1, max_length=40)
    bio: str = Field(default="", max_length=160)
    avatar_url: str | None = Field(default=None, min_length=1, max_length=2048)

    @field_validator(
        "creator_slug",
        "display_name",
        "bio",
        "avatar_url",
        mode="before",
    )
    @classmethod
    def _trim_creator_text(cls, value: object) -> object:
        return _trim_text(value)


class TravelNoteCreatorPage(StrictSchema):
    creator: TravelNoteCreator
    items: list[TravelNoteCard]
    next_cursor: str | None = None


class TravelNoteComment(StrictSchema):
    id: UUID
    note_id: UUID
    author_display_name: str = Field(min_length=1, max_length=40)
    body: str = Field(min_length=1, max_length=500)
    status: TravelNoteCommentStatus
    published_at: datetime | None = None

    @field_validator("author_display_name", "body", mode="before")
    @classmethod
    def _trim_comment_text(cls, value: object) -> object:
        return _trim_text(value)

    @model_validator(mode="after")
    def _validate_comment_lifecycle(self) -> "TravelNoteComment":
        if self.status == "pending_review" and self.published_at is not None:
            raise ValueError("pending review comments must not have a published_at timestamp")
        if self.status == "approved" and self.published_at is None:
            raise ValueError("approved comments must have a published_at timestamp")
        if self.status == "rejected" and self.published_at is not None:
            raise ValueError("rejected comments must not have a published_at timestamp")
        return self


def encode_travel_note_cursor(published_at: datetime, note_id: UUID) -> str:
    raw_cursor = f"{published_at.isoformat()}|{note_id}"
    return urlsafe_b64encode(raw_cursor.encode("utf-8")).decode("ascii").rstrip("=")


def decode_travel_note_cursor(cursor: str) -> tuple[datetime, UUID]:
    try:
        padding = "=" * (-len(cursor) % 4)
        decoded = urlsafe_b64decode((cursor + padding).encode("ascii")).decode("utf-8")
        published_at_raw, note_id_raw = decoded.split("|", maxsplit=1)
        return datetime.fromisoformat(published_at_raw), UUID(note_id_raw)
    except Exception as exc:  # pragma: no cover - invalid cursor path exercised by test
        raise ValueError("invalid travel note cursor") from exc
