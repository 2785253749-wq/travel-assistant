from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from fastapi.testclient import TestClient

from app.application.attraction_search import (
    AttractionCityApplicationRequest,
    AttractionNearbyApplicationRequest,
    AttractionSearchApplicationResult,
)
from app.attractions.models import AttractionSearchResult, AttractionSummary


FETCHED_AT = datetime(2026, 9, 11, 12, 0, tzinfo=UTC)


@dataclass
class FakeAttractionSearchApplication:
    result: AttractionSearchApplicationResult

    def __post_init__(self) -> None:
        self.city_requests: list[AttractionCityApplicationRequest] = []
        self.nearby_requests: list[AttractionNearbyApplicationRequest] = []

    def search_city(
        self, request: AttractionCityApplicationRequest
    ) -> AttractionSearchApplicationResult:
        self.city_requests.append(request)
        return self.result

    def search_nearby(
        self, request: AttractionNearbyApplicationRequest
    ) -> AttractionSearchApplicationResult:
        self.nearby_requests.append(request)
        return self.result


class FailIfCalledKnowledge:
    def answer(self, *_args, **_kwargs):
        raise AssertionError("attraction public flow must not call RAG")


def test_public_chat_city_attraction_search_uses_attraction_pipeline(
    client: TestClient,
    monkeypatch,
) -> None:
    from app import composition
    from app.schemas import ChatResponse

    fake_application = FakeAttractionSearchApplication(
        result=AttractionSearchApplicationResult(
            attractions=AttractionSearchResult(
                items=[
                    AttractionSummary(
                        id="fake-attraction-1",
                        name="测试景点",
                        rating=4.9,
                        provider="fake-attraction",
                    )
                ],
                total=1,
                page=1,
                page_size=10,
                provider="fake-attraction",
                status="success",
                warning=None,
                fetched_at=FETCHED_AT,
            ),
            mode="city",
            city="厦门",
            location_query=None,
            radius=None,
        )
    )
    monkeypatch.setattr(
        composition,
        "get_attraction_search_application",
        lambda: fake_application,
    )
    monkeypatch.setattr(composition, "get_hotel_nearby_application", lambda: None)
    monkeypatch.setattr(
        composition,
        "get_knowledge_answer_service",
        lambda: FailIfCalledKnowledge(),
    )
    monkeypatch.setattr(
        composition,
        "get_rag_v2_knowledge_service",
        lambda: FailIfCalledKnowledge(),
    )

    response = client.post(
        "/api/chat",
        json={"message": "厦门有哪些景点", "thread_id": "attraction-city-public"},
    )

    assert response.status_code == 200
    payload = response.json()
    ChatResponse.model_validate(payload)
    assert "测试景点" in payload["reply"]
    assert payload.get("warnings") is None
    assert fake_application.city_requests == [
        AttractionCityApplicationRequest(city="厦门")
    ]


def test_public_chat_nearby_attraction_search_uses_nearby_pipeline(
    client: TestClient,
    monkeypatch,
) -> None:
    from app import composition
    from app.schemas import ChatResponse

    fake_application = FakeAttractionSearchApplication(
        result=AttractionSearchApplicationResult(
            attractions=AttractionSearchResult(
                items=[
                    AttractionSummary(
                        id="fake-nearby-attraction-1",
                        name="附近测试景点",
                        rating=4.8,
                        provider="fake-attraction",
                    )
                ],
                total=1,
                page=1,
                page_size=10,
                provider="fake-attraction",
                status="success",
                warning=None,
                fetched_at=FETCHED_AT,
            ),
            mode="nearby",
            city="厦门",
            location_query="厦门大学",
            radius=2000,
        )
    )
    monkeypatch.setattr(
        composition,
        "get_attraction_search_application",
        lambda: fake_application,
    )
    monkeypatch.setattr(composition, "get_hotel_nearby_application", lambda: None)
    monkeypatch.setattr(
        composition,
        "get_knowledge_answer_service",
        lambda: FailIfCalledKnowledge(),
    )
    monkeypatch.setattr(
        composition,
        "get_rag_v2_knowledge_service",
        lambda: FailIfCalledKnowledge(),
    )

    response = client.post(
        "/api/chat",
        json={
            "message": "厦门大学附近有什么景点",
            "thread_id": "attraction-nearby-public",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    ChatResponse.model_validate(payload)
    assert "附近测试景点" in payload["reply"]
    assert payload.get("warnings") is None
    assert fake_application.nearby_requests == [
        AttractionNearbyApplicationRequest(
            location_query="厦门大学",
            city="厦门",
            radius=2000,
        )
    ]
