from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field, field_validator

from app.schemas import StrictSchema


def _strip_required_text(value: object) -> object:
    if isinstance(value, str):
        value = value.strip()
        if not value:
            raise ValueError("value must not be blank")
    return value


def _normalize_keyword(value: object) -> object:
    if isinstance(value, str):
        value = value.strip()
        return value or "酒店"
    return value


class HotelSearchRequest(StrictSchema):
    city: str = Field(min_length=1, max_length=80)
    keyword: str = Field(default="酒店", min_length=1, max_length=80)
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=10, ge=1, le=20)

    _strip_city = field_validator("city", mode="before")(_strip_required_text)
    _normalize_search_keyword = field_validator("keyword", mode="before")(
        _normalize_keyword
    )


class HotelNearbySearchRequest(StrictSchema):
    latitude: float = Field(ge=-90, le=90, allow_inf_nan=False)
    longitude: float = Field(ge=-180, le=180, allow_inf_nan=False)
    radius: int = Field(default=2000, ge=1, le=20_000)
    keyword: str = Field(default="酒店", min_length=1, max_length=80)
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=10, ge=1, le=20)

    _normalize_search_keyword = field_validator("keyword", mode="before")(
        _normalize_keyword
    )


class HotelSummary(StrictSchema):
    id: str = Field(min_length=1, max_length=200)
    name: str = Field(min_length=1, max_length=200)
    address: str | None = Field(default=None, max_length=500)
    latitude: float | None = Field(
        default=None, ge=-90, le=90, allow_inf_nan=False
    )
    longitude: float | None = Field(
        default=None, ge=-180, le=180, allow_inf_nan=False
    )
    rating: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    telephone: str | None = Field(default=None, max_length=200)
    distance: int | None = Field(default=None, ge=0)
    provider: str = Field(min_length=1, max_length=80)

    _strip_id = field_validator("id", mode="before")(_strip_required_text)
    _strip_name = field_validator("name", mode="before")(_strip_required_text)


class HotelDetail(HotelSummary):
    tags: list[str] = Field(default_factory=list, max_length=40)
    business_hours: str | None = Field(default=None, max_length=200)
    description: str | None = Field(default=None, max_length=1_000)
    detail_url: str | None = Field(default=None, max_length=2_048)

    @field_validator("tags", mode="before")
    @classmethod
    def _clean_tags(cls, value: object) -> object:
        if not isinstance(value, (list, tuple)):
            return value
        cleaned: list[str] = []
        for tag in value:
            if not isinstance(tag, str):
                raise ValueError("tags must contain strings")
            normalized = tag.strip()
            if normalized:
                cleaned.append(normalized)
        return cleaned


class HotelSearchResult(StrictSchema):
    items: list[HotelSummary] = Field(default_factory=list, max_length=20)
    total: int | None = Field(default=None, ge=0)
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=20)
    provider: str = Field(min_length=1, max_length=80)
    status: Literal["success", "unavailable"]
    warning: str | None = Field(default=None, max_length=500)
    fetched_at: datetime
