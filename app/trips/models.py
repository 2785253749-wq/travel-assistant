from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal
from uuid import UUID, uuid4

from app.schemas import Itinerary, TravelProfile


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


@dataclass
class ConversationMessage:
    user_id: UUID
    trip_id: UUID
    role: Literal["user", "assistant"]
    content: str
    id: UUID = field(default_factory=uuid4)
    created_at: datetime | None = None


@dataclass
class ShareLink:
    user_id: UUID
    trip_id: UUID
    token_hash: str
    expires_at: datetime
    id: UUID = field(default_factory=uuid4)
    revoked_at: datetime | None = None
    created_at: datetime | None = None
