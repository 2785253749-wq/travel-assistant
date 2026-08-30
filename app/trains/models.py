from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


TrainType = Literal["G", "D", "C", "Z", "T", "K", "O", "F", "S"]
DepartureTimeRange = Literal["凌晨", "上午", "下午", "晚上"]
TrainPreference = Literal["default", "cheapest", "fastest", "earliest_arrival"]
TrainAvailability = Literal["available", "unavailable", "unknown"]
TrainSearchStatus = Literal["success", "unavailable"]
TrainReasonCode = Literal[
    "time_fit",
    "shorter_duration",
    "lower_price",
    "seat_available",
    "earlier_arrival",
]


class TrainSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TrainQuery(TrainSchema):
    departure_station: str = Field(min_length=1, max_length=80)
    arrival_station: str = Field(min_length=1, max_length=80)
    travel_date: date
    train_types: tuple[TrainType, ...] | None = None
    departure_time_range: DepartureTimeRange | None = None
    seat_type: str | None = Field(default=None, min_length=1, max_length=40)
    require_available: bool = False
    preference: TrainPreference = "default"


class TrainSeat(TrainSchema):
    seat_name: str = Field(min_length=1, max_length=40)
    price_cny: Decimal | None = None
    remaining_label: str | None = Field(default=None, max_length=40)
    availability: TrainAvailability = "unknown"


class TrainOption(TrainSchema):
    option_id: str = Field(min_length=1, max_length=160)
    train_no: str = Field(min_length=1, max_length=20)
    departure_station: str = Field(min_length=1, max_length=80)
    arrival_station: str = Field(min_length=1, max_length=80)
    departure_station_code: str | None = Field(default=None, max_length=20)
    arrival_station_code: str | None = Field(default=None, max_length=20)
    departure_at: datetime
    arrival_at: datetime
    duration_minutes: int | None = Field(default=None, ge=0, le=7_200)
    bookable: bool | None = None
    seats: list[TrainSeat] = Field(default_factory=list, max_length=40)
    train_flags: list[str] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def _times_are_timezone_aware_and_chronological(self) -> "TrainOption":
        if self.departure_at.tzinfo is None or self.arrival_at.tzinfo is None:
            raise ValueError("train times must include a timezone")
        if self.arrival_at <= self.departure_at:
            raise ValueError("arrival_at must be after departure_at")
        return self


class TrainRecommendation(TrainSchema):
    selected_option_id: str = Field(min_length=1, max_length=160)
    reason_codes: list[TrainReasonCode] = Field(default_factory=list, max_length=5)


class TrainSearchResult(TrainSchema):
    query: TrainQuery
    options: list[TrainOption] = Field(default_factory=list, max_length=15)
    recommendation_candidates: list[TrainOption] = Field(default_factory=list, max_length=5)
    recommendation: TrainRecommendation | None = None
    fetched_at: datetime
    source: str = Field(min_length=8, max_length=2_048)
    status: TrainSearchStatus
    warning: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def _recommendation_must_reference_an_option(self) -> "TrainSearchResult":
        if self.recommendation is not None:
            option_ids = {option.option_id for option in self.options}
            if self.recommendation.selected_option_id not in option_ids:
                raise ValueError("recommendation must reference an option")
        return self
