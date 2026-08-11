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


def test_retrieved_chunk_requires_chinese_source_label():
    """Guards anonymous RAG facts that cannot be attributed in the interface."""
    with pytest.raises(ValidationError):
        RetrievedChunk(chunk_id="x", content="事实", source_label="", score=0.9)


def test_retrieved_chunk_rejects_scores_outside_similarity_range():
    """Guards invalid relevance scores from leaking through retrieval boundaries."""
    with pytest.raises(ValidationError):
        RetrievedChunk(chunk_id="x", content="事实", source_label="官方来源", score=1.01)


def test_knowledge_document_and_chunk_reject_unknown_fields():
    """Guards an unstable cross-task RAG data contract."""
    document = KnowledgeDocument(
        document_id="document-1",
        source_label="官方来源",
        content="可公开核验的旅行信息",
        published_at=datetime(2026, 8, 12),
    )
    chunk = KnowledgeChunk(
        chunk_id="chunk-1",
        document_id=document.document_id,
        content=document.content,
        source_label=document.source_label,
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
    itinerary_weather = ItineraryWeather(date=date(2026, 8, 12), weather=card)

    assert itinerary_weather.weather.status == "available"
    with pytest.raises(ValidationError):
        WeatherCard(city="北京", status="unknown", summary="晴")


def test_weather_payload_models_are_public_schemas():
    """Guards weather cards from bypassing the strict API payload boundary."""
    from app.schemas import ItineraryWeather as PublicItineraryWeather
    from app.schemas import WeatherCard as PublicWeatherCard

    card = PublicWeatherCard(city="北京", status="seasonal", summary="夏季多雨")
    payload = PublicItineraryWeather(date=date(2026, 8, 12), weather=card)

    assert payload.weather.status == "seasonal"
