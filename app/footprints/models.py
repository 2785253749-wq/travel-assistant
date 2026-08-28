from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import Field

from app.schemas import StrictSchema


class FootprintCreate(StrictSchema):
    city_adcode: str = Field(pattern=r"^\d{6}$")
    visited_at: date


class FootprintUpdate(StrictSchema):
    visited_at: date


class CityRecord(StrictSchema):
    city_adcode: str = Field(pattern=r"^\d{6}$")
    city_name: str = Field(min_length=1, max_length=40)
    province_adcode: str = Field(pattern=r"^\d{6}$")
    province_name: str = Field(min_length=1, max_length=40)
    center: tuple[float, float]


class FootprintView(StrictSchema):
    id: UUID
    city_adcode: str = Field(pattern=r"^\d{6}$")
    city_name: str = Field(min_length=1, max_length=40)
    province_adcode: str = Field(pattern=r"^\d{6}$")
    province_name: str = Field(min_length=1, max_length=40)
    center: tuple[float, float]
    visited_at: date
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class StoredFootprint:
    id: UUID
    user_id: UUID
    city_adcode: str
    city_name: str
    province_adcode: str
    province_name: str
    center: tuple[float, float]
    visited_at: date
    created_at: datetime
    updated_at: datetime


class DistrictBoundary(StrictSchema):
    city: CityRecord
    rings: list[list[tuple[float, float]]]
    fetched_at: datetime


class DistrictBoundaryView(StrictSchema):
    city: CityRecord
    rings: list[list[tuple[float, float]]]
    status: Literal["fresh", "stale", "unavailable"]
