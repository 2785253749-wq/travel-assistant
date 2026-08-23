from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import Field, field_validator

from app.schemas import (
    PROFILE_TRAVEL_STYLES_MAX_ITEMS,
    ProfileBio,
    ProfileDisplayName,
    ProfileHomeCity,
    StrictSchema,
    TravelStyle,
)


class ProfileInput(StrictSchema):
    display_name: ProfileDisplayName
    bio: ProfileBio
    home_city: ProfileHomeCity
    travel_styles: list[TravelStyle] = Field(
        max_length=PROFILE_TRAVEL_STYLES_MAX_ITEMS
    )
    avatar_path: str | None = Field(default=None, min_length=5, max_length=500)

    @field_validator("display_name", "bio", "home_city", "avatar_path", mode="before")
    @classmethod
    def _trim_text_fields(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip()
        return value


class UserProfile(StrictSchema):
    user_id: UUID
    email: str | None = Field(default=None, min_length=3, max_length=320)
    display_name: ProfileDisplayName
    bio: ProfileBio
    home_city: ProfileHomeCity
    travel_styles: list[TravelStyle] = Field(
        max_length=PROFILE_TRAVEL_STYLES_MAX_ITEMS
    )
    avatar_url: str | None = Field(default=None, min_length=1, max_length=2048)
    updated_at: datetime | None = None
