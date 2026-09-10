from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

import pytest

from app.agent.attraction_search_query import AttractionSearchQueryExtraction
from app.agent.graph import SafeTravelAgent
from app.agent.intent import IntentResult
from app.application.attraction_search import (
    AttractionCityApplicationRequest,
    AttractionNearbyApplicationRequest,
    AttractionSearchApplicationResult,
)
from app.attractions.models import AttractionSearchResult
from app.core.errors import AppError
from app.locations.models import LocationCandidate
from app.locations.service import LocationServiceError


FETCHED_AT = datetime(2026, 9, 10, 12, 0, tzinfo=UTC)


class AttractionIntentClassifier:
    def classify(self, _message: str, _has_trip: bool) -> IntentResult:
        return IntentResult(intent="attraction_search", confidence=1.0)


class FailIfCalledKnowledge:
    def answer(self, *_args, **_kwargs):
        raise AssertionError("RAG must not be called")


class FailIfCalledTravelExtractor:
    def extract(self, *_args, **_kwargs):
        raise AssertionError("profile extraction must not be called")


@dataclass
class FakeAttractionSearchExtractor:
    extraction: AttractionSearchQueryExtraction

    def extract(self, _message: str) -> AttractionSearchQueryExtraction:
        return self.extraction


@dataclass
class FakeAttractionSearchApplication:
    result: object
    error: Exception | None = None

    def __post_init__(self) -> None:
        self.city_requests: list[AttractionCityApplicationRequest] = []
        self.nearby_requests: list[AttractionNearbyApplicationRequest] = []

    def search_city(
        self, request: AttractionCityApplicationRequest
    ) -> object:
        self.city_requests.append(request)
        if self.error is not None:
            raise self.error
        return self.result

    def search_nearby(
        self, request: AttractionNearbyApplicationRequest
    ) -> object:
        self.nearby_requests.append(request)
        if self.error is not None:
            raise self.error
        return self.result


@dataclass
class FakeAttractionReplyRenderer:
    reply: str = "景点查询结果"

    def __post_init__(self) -> None:
        self.calls: list[object] = []

    def render(self, result: object) -> str:
        self.calls.append(result)
        return self.reply


def _application_result(
    *,
    status: str = "success",
    warning: str | None = None,
) -> AttractionSearchApplicationResult:
    return AttractionSearchApplicationResult(
        attractions=AttractionSearchResult(
            items=[],
            total=None,
            page=1,
            page_size=10,
            provider="fake-attraction",
            status=status,  # type: ignore[arg-type]
            warning=warning,
            fetched_at=FETCHED_AT,
        ),
        mode="city",
        city="厦门",
        location_query=None,
        radius=None,
    )


def _agent(
    extraction: AttractionSearchQueryExtraction,
    application: FakeAttractionSearchApplication | None,
    renderer: FakeAttractionReplyRenderer | None = None,
) -> SafeTravelAgent:
    return SafeTravelAgent(
        classifier=AttractionIntentClassifier(),
        extractor=FailIfCalledTravelExtractor(),
        knowledge=FailIfCalledKnowledge(),
        rag_v2_knowledge=FailIfCalledKnowledge(),
        attraction_search_extractor=FakeAttractionSearchExtractor(extraction),
        attraction_search_application=application,
        attraction_search_renderer=renderer or FakeAttractionReplyRenderer(),
    )


def test_city_attraction_query_calls_application_and_renderer_not_rag() -> None:
    application = FakeAttractionSearchApplication(_application_result())
    renderer = FakeAttractionReplyRenderer()
    agent = _agent(
        AttractionSearchQueryExtraction(mode="city", city="厦门"),
        application,
        renderer,
    )

    result = agent.collect("厦门有哪些景点", trip=None)

    assert application.city_requests == [
        AttractionCityApplicationRequest(city="厦门")
    ]
    assert renderer.calls == [application.result]
    assert result.reply == "景点查询结果"
    assert result.intent == "attraction_search"
    assert result.error_code is None


def test_city_attraction_rating_is_forwarded() -> None:
    application = FakeAttractionSearchApplication(_application_result())
    agent = _agent(
        AttractionSearchQueryExtraction(
            mode="city",
            city="厦门",
            sort_by="rating",
        ),
        application,
    )

    result = agent.collect("厦门评分最高的景点", trip=None)

    assert application.city_requests == [
        AttractionCityApplicationRequest(city="厦门", sort_by="rating")
    ]
    assert result.intent == "attraction_search"


def test_city_attraction_does_not_start_profile_collection() -> None:
    application = FakeAttractionSearchApplication(_application_result())
    agent = _agent(
        AttractionSearchQueryExtraction(mode="city", city="厦门"),
        application,
    )

    result = agent.collect("厦门有哪些景点", trip=None)

    assert result.reply == "景点查询结果"
    assert application.city_requests


def test_city_attraction_missing_city_clarifies_without_application() -> None:
    application = FakeAttractionSearchApplication(_application_result())
    agent = _agent(
        AttractionSearchQueryExtraction(mode="city", city=None),
        application,
    )

    result = agent.collect("有哪些景点", trip=None)

    assert application.city_requests == []
    assert "城市" in result.reply
    assert result.intent == "attraction_search"


def test_city_distance_invalid_never_reaches_application() -> None:
    application = FakeAttractionSearchApplication(_application_result())
    agent = _agent(
        AttractionSearchQueryExtraction(
            mode="city",
            city="厦门",
            sort_by="distance",
            invalid_fields=("sort_by",),
        ),
        application,
    )

    result = agent.collect("厦门最近的景点", trip=None)

    assert application.city_requests == []
    assert "不支持按距离排序" in result.reply
    assert result.intent == "attraction_search"


def test_city_attraction_application_missing_is_safe() -> None:
    result = _agent(
        AttractionSearchQueryExtraction(mode="city", city="厦门"),
        application=None,
    ).collect("厦门有哪些景点", trip=None)

    assert "暂不可用" in result.reply
    assert "行程" not in result.reply
    assert result.intent == "attraction_search"


def test_city_attraction_provider_error_is_safe_and_does_not_fallback_rag() -> None:
    application = FakeAttractionSearchApplication(
        _application_result(),
        error=AppError("BAIDU_ATTRACTION_TIMEOUT", "provider timed out"),
    )
    result = _agent(
        AttractionSearchQueryExtraction(mode="city", city="厦门"),
        application,
    ).collect("厦门有哪些景点", trip=None)

    assert "暂不可用" in result.reply
    assert "provider timed out" not in result.reply
    assert result.intent == "attraction_search"


def test_city_unavailable_result_is_rendered_without_rag() -> None:
    application_result = _application_result(
        status="unavailable",
        warning="BAIDU_ATTRACTION_NOT_CONFIGURED",
    )
    application = FakeAttractionSearchApplication(application_result)
    renderer = FakeAttractionReplyRenderer(reply="厦门的景点信息暂不可用。")
    result = _agent(
        AttractionSearchQueryExtraction(mode="city", city="厦门"),
        application,
        renderer,
    ).collect("厦门有哪些景点", trip=None)

    assert renderer.calls == [application_result]
    assert result.reply == "厦门的景点信息暂不可用。"
    assert result.intent == "attraction_search"


def test_run_city_attraction_query_uses_attraction_branch() -> None:
    application = FakeAttractionSearchApplication(_application_result())
    renderer = FakeAttractionReplyRenderer()
    result = _agent(
        AttractionSearchQueryExtraction(mode="city", city="厦门"),
        application,
        renderer,
    ).run("厦门有哪些景点", trip=None)

    assert result.reply == "景点查询结果"
    assert result.intent == "attraction_search"
    assert application.city_requests == [
        AttractionCityApplicationRequest(city="厦门")
    ]
    assert renderer.calls == [application.result]


def test_nearby_attraction_does_not_call_city_application() -> None:
    application = FakeAttractionSearchApplication(_application_result())
    result = _agent(
        AttractionSearchQueryExtraction(
            mode="nearby",
            city="厦门",
            location_query="厦门大学",
        ),
        application,
    ).collect("厦门大学附近有什么景点", trip=None)

    assert application.city_requests == []
    assert application.nearby_requests
    assert result.intent == "attraction_search"


def test_nearby_attraction_query_calls_nearby_application_and_renderer() -> None:
    application = FakeAttractionSearchApplication(_application_result())
    renderer = FakeAttractionReplyRenderer()
    result = _agent(
        AttractionSearchQueryExtraction(
            mode="nearby",
            city="厦门",
            location_query="厦门大学",
        ),
        application,
        renderer,
    ).collect("厦门大学附近有什么景点", trip=None)

    assert application.nearby_requests == [
        AttractionNearbyApplicationRequest(
            location_query="厦门大学",
            city="厦门",
            radius=2000,
        )
    ]
    assert renderer.calls == [application.result]
    assert result.reply == "景点查询结果"
    assert result.intent == "attraction_search"


def test_nearby_attraction_query_forwards_explicit_radius() -> None:
    application = FakeAttractionSearchApplication(_application_result())
    _agent(
        AttractionSearchQueryExtraction(
            mode="nearby",
            city="厦门",
            location_query="厦门大学",
            radius=3000,
        ),
        application,
    ).collect("厦门大学附近3公里有什么景点", trip=None)

    assert application.nearby_requests[0].radius == 3000


def test_nearby_attraction_query_forwards_rating() -> None:
    application = FakeAttractionSearchApplication(_application_result())
    _agent(
        AttractionSearchQueryExtraction(
            mode="nearby",
            city="厦门",
            location_query="厦门大学",
            sort_by="rating",
        ),
        application,
    ).collect("厦门大学附近评分最高的景点", trip=None)

    assert application.nearby_requests[0].sort_by == "rating"


def test_nearby_attraction_query_forwards_distance() -> None:
    application = FakeAttractionSearchApplication(_application_result())
    _agent(
        AttractionSearchQueryExtraction(
            mode="nearby",
            city="厦门",
            location_query="厦门大学",
            sort_by="distance",
        ),
        application,
    ).collect("厦门大学附近最近的景点", trip=None)

    assert application.nearby_requests[0].sort_by == "distance"


def test_nearby_attraction_without_city_clarifies_without_application() -> None:
    application = FakeAttractionSearchApplication(_application_result())
    result = _agent(
        AttractionSearchQueryExtraction(
            mode="nearby",
            city=None,
            location_query="鼓浪屿",
        ),
        application,
    ).collect("鼓浪屿附近有什么景点", trip=None)

    assert application.nearby_requests == []
    assert "城市" in result.reply or "所在城市" in result.reply
    assert result.intent == "attraction_search"


def test_nearby_attraction_without_location_clarifies_without_application() -> None:
    application = FakeAttractionSearchApplication(_application_result())
    result = _agent(
        AttractionSearchQueryExtraction(mode="nearby", city="厦门"),
        application,
    ).collect("厦门附近有什么景点", trip=None)

    assert application.nearby_requests == []
    assert "地点" in result.reply
    assert result.intent == "attraction_search"


def test_nearby_invalid_radius_clarifies_without_application() -> None:
    application = FakeAttractionSearchApplication(_application_result())
    result = _agent(
        AttractionSearchQueryExtraction(
            mode="nearby",
            city="厦门",
            location_query="厦门大学",
            radius=100,
            invalid_fields=("radius",),
        ),
        application,
    ).collect("厦门大学附近100米有什么景点", trip=None)

    assert application.nearby_requests == []
    assert "500 米" in result.reply or "20 公里" in result.reply
    assert "不支持按距离排序" not in result.reply


def test_nearby_location_not_found_preserves_error_code() -> None:
    application = FakeAttractionSearchApplication(
        _application_result(),
        error=LocationServiceError("LOCATION_NOT_FOUND"),
    )
    renderer = FakeAttractionReplyRenderer()
    result = _agent(
        AttractionSearchQueryExtraction(
            mode="nearby",
            city="厦门",
            location_query="厦门大学",
        ),
        application,
        renderer,
    ).collect("厦门大学附近有什么景点", trip=None)

    assert result.error_code == "LOCATION_NOT_FOUND"
    assert result.intent == "attraction_search"
    assert "未找到" in result.reply
    assert renderer.calls == []


def test_nearby_location_ambiguous_preserves_error_code_without_pending_state() -> None:
    candidates = [
        LocationCandidate(
            id="candidate-1",
            name="鼓浪屿风景名胜区",
            latitude=24.45,
            longitude=118.07,
            provider="fake-location",
        ),
        LocationCandidate(
            id="candidate-2",
            name="厦门鼓浪屿钢琴码头",
            latitude=24.46,
            longitude=118.08,
            provider="fake-location",
        ),
    ]
    application = FakeAttractionSearchApplication(
        _application_result(),
        error=LocationServiceError("LOCATION_AMBIGUOUS", candidates=candidates),
    )
    renderer = FakeAttractionReplyRenderer()
    result = _agent(
        AttractionSearchQueryExtraction(
            mode="nearby",
            city="厦门",
            location_query="厦门鼓浪屿",
        ),
        application,
        renderer,
    ).collect("厦门鼓浪屿附近有什么景点", trip=None)

    assert result.error_code == "LOCATION_AMBIGUOUS"
    assert result.intent == "attraction_search"
    assert "鼓浪屿风景名胜区" in result.reply
    assert "厦门鼓浪屿钢琴码头" in result.reply
    assert "candidate-1" not in result.reply
    assert "candidate-2" not in result.reply
    assert renderer.calls == []


def test_nearby_provider_error_is_safe_and_does_not_fallback_rag() -> None:
    application = FakeAttractionSearchApplication(
        _application_result(),
        error=AppError("BAIDU_ATTRACTION_TIMEOUT", "provider timed out"),
    )
    result = _agent(
        AttractionSearchQueryExtraction(
            mode="nearby",
            city="厦门",
            location_query="厦门大学",
        ),
        application,
    ).collect("厦门大学附近有什么景点", trip=None)

    assert "暂不可用" in result.reply
    assert "provider timed out" not in result.reply
    assert result.intent == "attraction_search"


def test_nearby_unavailable_result_is_rendered_without_rag() -> None:
    application_result = _application_result(
        status="unavailable",
        warning="BAIDU_ATTRACTION_NOT_CONFIGURED",
    )
    application = FakeAttractionSearchApplication(application_result)
    renderer = FakeAttractionReplyRenderer(reply="附近景点信息暂不可用。")
    result = _agent(
        AttractionSearchQueryExtraction(
            mode="nearby",
            city="厦门",
            location_query="厦门大学",
        ),
        application,
        renderer,
    ).collect("厦门大学附近有什么景点", trip=None)

    assert renderer.calls == [application_result]
    assert result.reply == "附近景点信息暂不可用。"
    assert result.intent == "attraction_search"


def test_run_nearby_attraction_query_uses_nearby_branch() -> None:
    application = FakeAttractionSearchApplication(_application_result())
    renderer = FakeAttractionReplyRenderer()
    result = _agent(
        AttractionSearchQueryExtraction(
            mode="nearby",
            city="厦门",
            location_query="厦门大学",
        ),
        application,
        renderer,
    ).run("厦门大学附近有什么景点", trip=None)

    assert result.reply == "景点查询结果"
    assert result.intent == "attraction_search"
    assert application.nearby_requests == [
        AttractionNearbyApplicationRequest(
            location_query="厦门大学",
            city="厦门",
            radius=2000,
        )
    ]
    assert renderer.calls == [application.result]
