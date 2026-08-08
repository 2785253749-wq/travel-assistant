from dataclasses import dataclass, field
from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID, uuid4

from pydantic import Field, TypeAdapter

from app.schemas import CHAT_REPLY_MAX_LENGTH, Itinerary, TravelProfile


TRIP_TITLE_MAX_LENGTH = 100
TripTitle = Annotated[
    str,
    Field(min_length=1, max_length=TRIP_TITLE_MAX_LENGTH),
]
_TRIP_TITLE_ADAPTER = TypeAdapter(TripTitle)
MessageContent = Annotated[
    str,
    Field(min_length=1, max_length=CHAT_REPLY_MAX_LENGTH),
]
_MESSAGE_CONTENT_ADAPTER = TypeAdapter(MessageContent)


def validate_trip_title(value: object) -> str:
    """Apply the same title contract used by the database at every boundary."""
    return _TRIP_TITLE_ADAPTER.validate_python(value)


def destination_trip_title(destination: str | None) -> str:
    suffix = " trip"
    stem = destination or "New"
    return validate_trip_title(stem[: TRIP_TITLE_MAX_LENGTH - len(suffix)] + suffix)


def copied_trip_title(title: str) -> str:
    suffix = " (copy)"
    source = validate_trip_title(title)
    return validate_trip_title(
        source[: TRIP_TITLE_MAX_LENGTH - len(suffix)] + suffix
    )


@dataclass
class Trip:
    user_id: UUID
    title: str
    profile: TravelProfile
    id: UUID = field(default_factory=uuid4)
    status: Literal["collecting", "planned"] = "collecting"
    itinerary: Itinerary | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    def __post_init__(self) -> None:
        self.title = validate_trip_title(self.title)


@dataclass
class ConversationMessage:
    user_id: UUID
    trip_id: UUID
    role: Literal["user", "assistant"]
    content: str
    id: UUID = field(default_factory=uuid4)
    created_at: datetime | None = None

    def __post_init__(self) -> None:
        self.content = _MESSAGE_CONTENT_ADAPTER.validate_python(self.content)


@dataclass
class ShareLink:
    user_id: UUID
    trip_id: UUID
    token_hash: str
    expires_at: datetime
    id: UUID = field(default_factory=uuid4)
    revoked_at: datetime | None = None
    created_at: datetime | None = None
