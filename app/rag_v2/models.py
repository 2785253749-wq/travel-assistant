from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictStr, model_validator


class RagV2Schema(BaseModel):
    """Base model for RAG V2 values with a closed field contract."""

    model_config = ConfigDict(extra="forbid")


class DestinationLevel(str, Enum):
    province = "province"
    prefecture_city = "prefecture_city"
    autonomous_prefecture = "autonomous_prefecture"
    county_city = "county_city"


class ChunkType(str, Enum):
    overview = "overview"
    highlights = "highlights"
    transport = "transport"
    visit_advice = "visit_advice"
    seasonal = "seasonal"


class AttractionLifecycleStatus(str, Enum):
    active = "active"
    retired = "retired"
    merged = "merged"


class AttractionVersionStatus(str, Enum):
    included = "included"
    suppressed = "suppressed"


class ChunkStatus(str, Enum):
    pending = "pending"
    embedded = "embedded"
    failed = "failed"
    excluded = "excluded"


class EmbeddingTask(str, Enum):
    passage = "retrieval.passage"
    query = "retrieval.query"


AdministrativeCode = Annotated[StrictStr, Field(pattern=r"^\d{6}$")]


class Destination(RagV2Schema):
    destination_code: AdministrativeCode
    destination_level: DestinationLevel
    destination_name: str
    province_code: AdministrativeCode
    province_name: str
    district_name: str | None = None
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)


class EmbeddingProfile(RagV2Schema):
    model: str
    task: EmbeddingTask
    dimensions: int = Field(gt=0)
    input_schema_version: str


class SourceProvenance(RagV2Schema):
    source_type: str
    source_label: str
    source_url: str
    reviewed_on: date


class StableAttraction(RagV2Schema):
    attraction_id: UUID
    lifecycle_status: AttractionLifecycleStatus
    created_at: datetime
    retired_at: datetime | None = None
    merged_into_attraction_id: UUID | None = None

    @model_validator(mode="after")
    def validate_lifecycle_invariant(self) -> "StableAttraction":
        if self.lifecycle_status is AttractionLifecycleStatus.active:
            if self.retired_at is not None or self.merged_into_attraction_id is not None:
                raise ValueError("active attractions cannot have retirement or merge fields")
        elif self.lifecycle_status is AttractionLifecycleStatus.retired:
            if self.retired_at is None or self.merged_into_attraction_id is not None:
                raise ValueError("retired attractions require retired_at and cannot be merged")
        elif self.merged_into_attraction_id is None:
            raise ValueError("merged attractions require merged_into_attraction_id")

        if self.merged_into_attraction_id == self.attraction_id:
            raise ValueError("an attraction cannot merge into itself")
        return self


class AttractionVersionMetadata(RagV2Schema):
    attraction_id: UUID
    canonical_name: str
    aliases: tuple[str, ...]
    destination: Destination
    category: str | None = None
    tags: tuple[str, ...]
    status: AttractionVersionStatus


class SemanticSection(RagV2Schema):
    attraction_id: UUID
    chunk_type: ChunkType
    content: str
    source_label: str
    source_url: str
    source_type: str
    reviewed_on: date
