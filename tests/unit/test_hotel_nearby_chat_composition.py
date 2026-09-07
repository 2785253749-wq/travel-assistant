from __future__ import annotations


def test_chat_composition_injects_hotel_nearby_dependencies(monkeypatch) -> None:
    from app import composition
    from app.schemas import TravelProfile

    application = object()
    extractor = object()
    renderer = object()
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        composition,
        "get_hotel_nearby_application",
        lambda: application,
        raising=False,
    )
    monkeypatch.setattr(
        composition,
        "HotelNearbyQueryExtractor",
        lambda: extractor,
        raising=False,
    )
    monkeypatch.setattr(
        composition,
        "HotelNearbyReplyRenderer",
        lambda: renderer,
        raising=False,
    )

    class CapturingAgent:
        def __init__(self, **kwargs) -> None:
            captured.update(kwargs)

    monkeypatch.setattr(composition, "SafeTravelAgent", CapturingAgent)

    chat_application = composition.build_chat_application(None)
    chat_application._agent_factory(TravelProfile())

    assert captured["hotel_nearby_application"] is application
    assert captured["hotel_nearby_extractor"] is extractor
    assert captured["hotel_nearby_renderer"] is renderer


def test_chat_composition_without_baidu_keeps_rag_v2_and_disables_hotel(
    monkeypatch,
) -> None:
    from app import composition
    from app.core.config import Settings
    from app.schemas import TravelProfile

    settings = Settings(baidu_map_ak=None, _env_file=None)
    rag_v2_knowledge = object()

    monkeypatch.setattr(composition, "get_settings", lambda: settings)
    monkeypatch.setattr(composition, "get_provider_evidence_aggregator", object)
    monkeypatch.setattr(composition, "JuheTrainProvider", lambda **_: object())
    monkeypatch.setattr(composition, "TrainService", lambda **_: object())
    monkeypatch.setattr(composition, "get_knowledge_answer_service", object)
    monkeypatch.setattr(
        composition,
        "get_rag_v2_knowledge_service",
        lambda: rag_v2_knowledge,
    )
    monkeypatch.setattr(composition, "get_weather_service", object)
    monkeypatch.setattr(composition, "get_usage_guard", lambda: object())
    monkeypatch.setattr(composition, "HotelNearbyQueryExtractor", object)
    monkeypatch.setattr(composition, "HotelNearbyReplyRenderer", object)

    composition.get_hotel_nearby_application.cache_clear()
    try:
        chat_application = composition.build_chat_application(None)
        agent = chat_application._agent_factory(TravelProfile())
    finally:
        composition.get_hotel_nearby_application.cache_clear()

    assert agent._rag_v2_knowledge is rag_v2_knowledge
    assert agent._hotel_nearby_application is None
