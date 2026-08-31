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
