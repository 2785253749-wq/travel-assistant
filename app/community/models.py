from __future__ import annotations

from base64 import urlsafe_b64decode, urlsafe_b64encode
from datetime import datetime
from math import isfinite
from typing import Literal
from uuid import UUID

from pydantic import Field, field_validator

from app.schemas import JsonObject, StrictSchema


COMMUNITY_AUTHOR_DISPLAY_NAME_MAX_LENGTH = 40
COMMUNITY_TITLE_MAX_LENGTH = 100
COMMUNITY_DESTINATION_MAX_LENGTH = 80
COMMUNITY_SUMMARY_MAX_LENGTH = 300
COMMUNITY_ERROR_MESSAGE_MAX_LENGTH = 200
CommunityErrorCode = Literal[
    "COMMUNITY_POST_NOT_FOUND",
    "COMMUNITY_TRIP_NOT_PUBLISHABLE",
    "COMMUNITY_POST_EXISTS",
    "COMMUNITY_PUBLISH_FAILED",
    "COMMUNITY_VALIDATION_FAILED",
]


def _is_json_compatible(value: object) -> bool:
    if value is None or isinstance(value, (str, bool, int)):
        return True
    if isinstance(value, float):
        return isfinite(value)
    if isinstance(value, list):
        return all(_is_json_compatible(item) for item in value)
    if isinstance(value, dict):
        return all(
            isinstance(key, str) and _is_json_compatible(item)
            for key, item in value.items()
        )
    return False


class CommunityPost(StrictSchema):
    id: UUID
    author_display_name: str = Field(
        min_length=1, max_length=COMMUNITY_AUTHOR_DISPLAY_NAME_MAX_LENGTH
    )
    title: str = Field(min_length=1, max_length=COMMUNITY_TITLE_MAX_LENGTH)
    destination: str = Field(min_length=1, max_length=COMMUNITY_DESTINATION_MAX_LENGTH)
    summary: str = Field(min_length=1, max_length=COMMUNITY_SUMMARY_MAX_LENGTH)
    itinerary_snapshot: JsonObject
    created_at: datetime
    updated_at: datetime
    can_delete: bool = False

    @field_validator("itinerary_snapshot")
    @classmethod
    def _snapshot_must_be_json_compatible(cls, value: JsonObject) -> JsonObject:
        if not _is_json_compatible(value):
            raise ValueError("itinerary_snapshot must be a JSON-compatible object")
        return value


class CommunityPage(StrictSchema):
    items: list[CommunityPost]
    next_cursor: str | None = None


class CommunityPublishInput(StrictSchema):
    trip_id: UUID
    summary: str = Field(min_length=1, max_length=COMMUNITY_SUMMARY_MAX_LENGTH)


class CommunityPublishResult(StrictSchema):
    post: CommunityPost


class CommunityErrorDetail(StrictSchema):
    code: CommunityErrorCode
    message: str = Field(min_length=1, max_length=COMMUNITY_ERROR_MESSAGE_MAX_LENGTH)


class CommunityErrorResponse(StrictSchema):
    detail: CommunityErrorDetail


def encode_community_cursor(created_at: datetime, post_id: UUID) -> str:
    raw_cursor = f"{created_at.isoformat()}|{post_id}"
    return urlsafe_b64encode(raw_cursor.encode("utf-8")).decode("ascii").rstrip("=")


def decode_community_cursor(cursor: str) -> tuple[datetime, UUID]:
    try:
        padding = "=" * (-len(cursor) % 4)
        decoded = urlsafe_b64decode((cursor + padding).encode("ascii")).decode("utf-8")
        created_at_raw, post_id_raw = decoded.split("|", maxsplit=1)
        return datetime.fromisoformat(created_at_raw), UUID(post_id_raw)
    except Exception as exc:  # pragma: no cover - exercised by invalid-cursor test
        raise ValueError("invalid community cursor") from exc
