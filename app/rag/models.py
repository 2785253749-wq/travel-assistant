from __future__ import annotations

from datetime import date, datetime

from pydantic import Field

from app.schemas import ItineraryWeather, StrictSchema, WeatherCard


class KnowledgeDocument(StrictSchema):
    document_id: str = Field(min_length=1, max_length=200)
    title: str = Field(min_length=1, max_length=300)
    document_version: str = Field(min_length=1, max_length=100)
    region: str = Field(min_length=1, max_length=100)
    topic: str = Field(min_length=1, max_length=100)
    source_label: str = Field(min_length=1, max_length=120)
    content: str = Field(min_length=1, max_length=20_000)
    published_at: datetime | None = None
    reviewed_on: date


class KnowledgeChunk(StrictSchema):
    chunk_id: str = Field(min_length=1, max_length=200)
    document_id: str = Field(min_length=1, max_length=200)
    title: str = Field(min_length=1, max_length=300)
    document_version: str = Field(min_length=1, max_length=100)
    region: str = Field(min_length=1, max_length=100)
    topic: str = Field(min_length=1, max_length=100)
    content: str = Field(min_length=1, max_length=6000)
    source_label: str = Field(min_length=1, max_length=120)
    reviewed_on: date


class RetrievedChunk(StrictSchema):
    chunk_id: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1, max_length=6000)
    source_label: str = Field(min_length=1, max_length=120)
    score: float = Field(ge=0.0, le=1.0)
