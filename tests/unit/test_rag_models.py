from datetime import date, datetime

import pytest
from pydantic import ValidationError

from app.rag.models import (
    ItineraryWeather,
    KnowledgeChunk,
    KnowledgeDocument,
    RetrievedChunk,
    WeatherCard,
)
from app.schemas import Activity, ItineraryDay


def test_retrieved_chunk_requires_non_empty_source_label():
    """Guards anonymous RAG facts that cannot be attributed in the interface."""
    with pytest.raises(ValidationError):
        RetrievedChunk(chunk_id="x", content="事实", source_label="", score=0.9)


def test_retrieved_chunk_rejects_scores_outside_similarity_range():
    """Guards invalid relevance scores from leaking through retrieval boundaries."""
    with pytest.raises(ValidationError):
        RetrievedChunk(chunk_id="x", content="事实", source_label="官方来源", score=1.01)


def test_knowledge_document_and_chunk_preserve_versioned_regional_metadata():
    """Guards Task 2 region retrieval and same-version idempotent imports."""
    document = KnowledgeDocument(
        document_id="document-1",
        title="福建旅行须知",
        document_version="2026-08",
        region="福建",
        topic="出行",
        source_label="官方来源",
        content="可公开核验的旅行信息",
        published_at=datetime(2026, 8, 12),
        reviewed_on=date(2026, 8, 12),
    )
    chunk = KnowledgeChunk(
        chunk_id="chunk-1",
        document_id=document.document_id,
        title=document.title,
        document_version=document.document_version,
        region=document.region,
        topic=document.topic,
        content=document.content,
        source_label=document.source_label,
        reviewed_on=document.reviewed_on,
    )

    assert (document.region, document.document_version) == ("福建", "2026-08")
    assert (chunk.region, chunk.reviewed_on) == ("福建", date(2026, 8, 12))


def test_knowledge_document_and_chunk_reject_unknown_fields():
    """Guards an unstable cross-task RAG data contract."""
    document = KnowledgeDocument(
        document_id="document-1",
        title="福建旅行须知",
        document_version="2026-08",
        region="福建",
        topic="出行",
        source_label="官方来源",
        content="可公开核验的旅行信息",
        reviewed_on=date(2026, 8, 12),
    )
    chunk = KnowledgeChunk(
        chunk_id="chunk-1",
        document_id=document.document_id,
        title=document.title,
        document_version=document.document_version,
        region=document.region,
        topic=document.topic,
        content=document.content,
        source_label=document.source_label,
        reviewed_on=document.reviewed_on,
    )

    with pytest.raises(ValidationError):
        KnowledgeDocument(**document.model_dump(), unexpected="value")
    with pytest.raises(ValidationError):
        KnowledgeChunk(**chunk.model_dump(), unexpected="value")


def test_weather_card_and_itinerary_weather_preserve_weather_availability_contract():
    """Guards itinerary weather output from accepting non-contractual statuses."""
    card = WeatherCard(
        city="北京",
        status="available",
        summary="晴，适合步行游览。",
        report_time=datetime(2026, 8, 12, 9),
    )
    itinerary_weather = ItineraryWeather(
        date=date(2026, 8, 12),
        **card.model_dump(),
    )

    assert itinerary_weather.status == "available"
    with pytest.raises(ValidationError):
        WeatherCard(city="北京", status="unknown", summary="晴")


def test_itinerary_day_rejects_weather_before_task5_merges_it():
    """Guards the planning schema from accepting unproven model-invented weather."""
    weather = ItineraryWeather(
        date=date(2026, 8, 12),
        city="北京",
        status="seasonal",
        summary="夏季多雨",
    )
    with pytest.raises(ValidationError):
        ItineraryDay(
            date=date(2026, 8, 12),
            morning=Activity(title="早餐", start_time="08:00", end_time="09:00"),
            afternoon=Activity(title="参观", start_time="10:00", end_time="12:00"),
            evening=Activity(title="晚餐", start_time="18:00", end_time="19:00"),
            weather=weather,
        )


@pytest.mark.parametrize("sensitive_field", ["unexpected", "key", "raw_payload", "error_detail"])
def test_weather_payload_models_reject_extra_sensitive_fields(sensitive_field):
    """Guards public weather payloads against provider-secret and raw-data leaks."""
    weather = {
        "city": "北京",
        "status": "available",
        "summary": "晴",
    }

    with pytest.raises(ValidationError):
        WeatherCard(**weather, **{sensitive_field: "redacted"})
    with pytest.raises(ValidationError):
        ItineraryWeather(
            date=date(2026, 8, 12),
            **weather,
            **{sensitive_field: "redacted"},
        )


def test_weather_payload_models_serialize_only_the_public_field_whitelist():
    """Guards accidental addition of provider data to public weather JSON."""
    card = WeatherCard(city="北京", status="available", summary="晴")
    weather = ItineraryWeather(
        date=date(2026, 8, 12),
        city="北京",
        status="available",
        summary="晴",
    )

    assert set(card.model_dump()) == {"city", "status", "summary", "report_time"}
    assert set(weather.model_dump()) == {
        "date",
        "city",
        "status",
        "summary",
        "report_time",
    }
