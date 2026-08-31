"""Location coordinates exposed by this module use the GCJ-02 coordinate system."""

from __future__ import annotations

from pydantic import Field, field_validator

from app.schemas import StrictSchema


def _strip_required_text(value: object) -> object:
    if isinstance(value, str):
        value = value.strip()
        if not value:
            raise ValueError("value must not be blank")
    return value


def _strip_optional_text(value: object) -> object:
    if isinstance(value, str):
        value = value.strip()
        return value or None
    return value


class LocationQuery(StrictSchema):
    query: str = Field(min_length=1, max_length=200)
    city: str | None = Field(default=None, max_length=80)

    _strip_query = field_validator("query", mode="before")(_strip_required_text)
    _strip_city = field_validator("city", mode="before")(_strip_optional_text)


class LocationCandidate(StrictSchema):
    """A provider-independent POI candidate with GCJ-02 coordinates."""

    id: str | None = Field(default=None, max_length=200)
    name: str = Field(min_length=1, max_length=200)
    latitude: float = Field(ge=-90, le=90, allow_inf_nan=False)
    longitude: float = Field(ge=-180, le=180, allow_inf_nan=False)
    address: str | None = Field(default=None, max_length=500)
    city: str | None = Field(default=None, max_length=80)
    district: str | None = Field(default=None, max_length=80)
    province: str | None = Field(default=None, max_length=80)
    provider: str = Field(min_length=1, max_length=80)

    _strip_id = field_validator("id", mode="before")(_strip_optional_text)
    _strip_name = field_validator("name", mode="before")(_strip_required_text)
    _strip_optional_location_text = field_validator(
        "address", "city", "district", "province", mode="before"
    )(_strip_optional_text)
    _strip_provider = field_validator("provider", mode="before")(
        _strip_required_text
    )


class ResolvedLocation(LocationCandidate):
    """A candidate confirmed as usable by the location service."""


class LocationSearchResult(StrictSchema):
    items: list[LocationCandidate] = Field(default_factory=list)
    provider: str = Field(min_length=1, max_length=80)

    _strip_provider = field_validator("provider", mode="before")(
        _strip_required_text
    )
