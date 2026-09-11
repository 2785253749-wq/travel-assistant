from __future__ import annotations

from app.schemas import TravelProfile


def test_build_chat_application_injects_attraction_application(monkeypatch) -> None:
    from app import composition

    attraction_application = object()

    monkeypatch.setattr(
        composition,
        "get_attraction_search_application",
        lambda: attraction_application,
        raising=False,
    )
    monkeypatch.setattr(composition, "get_provider_evidence_aggregator", object)
    monkeypatch.setattr(composition, "get_knowledge_answer_service", lambda: None)
    monkeypatch.setattr(composition, "get_rag_v2_knowledge_service", lambda: None)
    monkeypatch.setattr(composition, "get_weather_service", lambda: None)
    monkeypatch.setattr(composition, "get_hotel_nearby_application", lambda: None)

    chat = composition.build_chat_application(user=None)
    agent = chat._agent_factory(TravelProfile())

    assert agent._attraction_search_application is attraction_application
