from __future__ import annotations

from datetime import date, datetime, time
from typing import Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, model_validator


class StrictSchema(BaseModel):
    """Public payload models reject unknown fields to keep API output stable."""

    model_config = ConfigDict(extra="forbid")

class TravelProfile(StrictSchema):
    origin: str | None = None
    destination: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    travelers: int | None = Field(default=None, ge=1)
    budget_cny: int | None = Field(default=None, ge=0)
    preferences: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)


class RawTravelProfile(StrictSchema):
    """Model output before product-boundary validation creates a TravelProfile."""

    origin: str | None = None
    destination: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    travelers: int | None = None
    budget_cny: int | None = Field(default=None, ge=0)
    preferences: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)


class ExtractionResult(StrictSchema):
    profile: RawTravelProfile


class ProfileIssue(StrictSchema):
    code: str
    field: str
    message: str

class ChatRequest(StrictSchema):
    message: str = Field(min_length=1, max_length=4000)
    thread_id: str = Field(min_length=1, max_length=100)

class ChatResponse(StrictSchema):
    reply: str
    stage: Literal["collecting", "planned"]
    profile: TravelProfile


class SourceCitation(StrictSchema):
    """Display metadata for a fact that was verified by trusted evidence."""

    evidence_id: str = Field(min_length=1, max_length=200)
    source_url: str = Field(
        min_length=8,
        max_length=2048,
        pattern=r"^https://",
        validation_alias=AliasChoices("source_url", "source"),
    )
    source_type: Literal["official", "government", "trusted_provider"]
    fetched_at: datetime
    freshness: str = Field(min_length=1, max_length=500)
    fact: str = Field(default="", max_length=1000)

    @property
    def source(self) -> str:
        """Readable source alias while the JSON contract remains `source_url`."""
        return self.source_url


class Activity(StrictSchema):
    title: str = Field(min_length=1, max_length=300)
    start_time: str = Field(pattern=r"^\d{2}:\d{2}$")
    end_time: str = Field(pattern=r"^\d{2}:\d{2}$")
    notes: list[str] = Field(default_factory=list, max_length=20)
    facts: list["FactClaim"] = Field(default_factory=list, max_length=20, validation_alias=AliasChoices("facts", "claims"))
    citations: list[SourceCitation] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def _end_is_after_start(self) -> "Activity":
        start = time.fromisoformat(self.start_time)
        end = time.fromisoformat(self.end_time)
        if end <= start:
            raise ValueError("activity end_time must be after start_time")
        return self

    @property
    def claims(self) -> list["FactClaim"]:
        return self.facts


class ItineraryDay(StrictSchema):
    date: date
    morning: Activity
    afternoon: Activity
    evening: Activity

    @model_validator(mode="after")
    def _activities_are_chronological(self) -> "ItineraryDay":
        activities = (self.morning, self.afternoon, self.evening)
        previous_end: time | None = None
        for activity in activities:
            start = time.fromisoformat(activity.start_time)
            end = time.fromisoformat(activity.end_time)
            if previous_end is not None and start < previous_end:
                raise ValueError("daily activities must not overlap")
            previous_end = end
        return self


class BudgetBreakdown(StrictSchema):
    """CNY estimate for the stated traveler basis, never a live quote."""

    transport: int = Field(ge=0)
    hotel: int = Field(ge=0)
    food: int = Field(ge=0)
    tickets: int = Field(ge=0)
    reserve: int = Field(ge=0)
    other: int = Field(ge=0)
    total: int = Field(ge=0)
    currency: Literal["CNY"]
    traveler_basis: Literal["trip_total", "per_person"]
    traveler_count: int = Field(ge=1, le=6)
    trip_total: int = Field(ge=0)
    estimate: "EstimateRange"

    @model_validator(mode="after")
    def _total_matches_categories(self) -> "BudgetBreakdown":
        categories = self.transport + self.hotel + self.food + self.tickets + self.reserve + self.other
        if self.total != categories:
            raise ValueError("budget total must equal its categories")
        expected_trip_total = self.total if self.traveler_basis == "trip_total" else self.total * self.traveler_count
        if self.trip_total != expected_trip_total:
            raise ValueError("trip_total must match the traveler basis")
        if self.estimate.currency != self.currency or self.estimate.basis != self.traveler_basis:
            raise ValueError("estimate currency and basis must match budget")
        if self.estimate.point != self.total:
            raise ValueError("estimate point must equal budget total")
        return self


class EstimateRange(StrictSchema):
    low: int = Field(ge=0)
    point: int = Field(ge=0)
    high: int = Field(ge=0)
    currency: Literal["CNY"]
    basis: Literal["trip_total", "per_person"]
    assumption_id: str = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def _contains_point(self) -> "EstimateRange":
        if not self.low <= self.point <= self.high:
            raise ValueError("estimate range must contain point")
        return self


class PlanningAssumption(StrictSchema):
    assumption_id: str = Field(min_length=1, max_length=100)
    category: Literal["budget", "transport", "pacing"]
    description: str = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def _is_not_a_variable_fact(self) -> "PlanningAssumption":
        forbidden = ("price", "availability", "open", "price", "价格", "营业", "可订", "库存", "余票")
        if any(term in self.description.lower() for term in forbidden):
            raise ValueError("assumptions cannot state variable facts")
        return self


class FactClaim(StrictSchema):
    text: str = Field(min_length=1, max_length=1000)
    evidence_id: str = Field(min_length=1, max_length=200)


class Itinerary(StrictSchema):
    title: str = Field(min_length=1, max_length=300)
    start_date: date
    end_date: date
    days: list[ItineraryDay] = Field(min_length=2, max_length=7)
    budget: BudgetBreakdown
    notes: list[str] = Field(default_factory=list, max_length=40)
    assumptions: list[PlanningAssumption] = Field(min_length=1, max_length=40)
    citations: list[SourceCitation] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def _days_cover_the_itinerary_range(self) -> "Itinerary":
        if self.end_date < self.start_date:
            raise ValueError("end_date must not be before start_date")
        expected_days = (self.end_date - self.start_date).days + 1
        if len(self.days) != expected_days:
            raise ValueError("days must match itinerary date range")
        for offset, day in enumerate(self.days):
            if day.date != self.start_date.fromordinal(self.start_date.toordinal() + offset):
                raise ValueError("itinerary day dates must be continuous")
        assumption_ids = [assumption.assumption_id for assumption in self.assumptions]
        if len(assumption_ids) != len(set(assumption_ids)):
            raise ValueError("assumption ids must be unique")
        if self.budget.estimate.assumption_id not in assumption_ids:
            raise ValueError("estimate assumption_id must reference an itinerary assumption")
        return self
