from __future__ import annotations

from dataclasses import dataclass

from app.agent.attraction_search_query import AttractionSearchQueryExtractor
from app.agent.graph import PendingHotelNearbySelection, RuleIntentClassifier, SafeTravelAgent
from app.application.attraction_search import AttractionNearbyApplicationRequest
from app.application.chat import ConfirmationStore, TravelChatApplication
from app.locations.models import LocationCandidate
from app.locations.service import LocationServiceError
from app.rag_v2.knowledge import V2KnowledgeResult
from app.schemas import TravelProfile


@dataclass
class FakeAttractionSearchApplication:
    error: Exception | None = None

    def __post_init__(self) -> None:
        self.requests: list[AttractionNearbyApplicationRequest] = []

    def search_nearby(self, request: AttractionNearbyApplicationRequest) -> object:
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        return object()


class FakeAttractionReplyRenderer:
    def render(self, _result: object) -> str:
        return "景点查询结果"


class RecordingRagAnswerer:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def answer(self, query: str, **_kwargs: object) -> V2KnowledgeResult:
        self.calls.append(query)
        return V2KnowledgeResult(
            status="grounded",
            reply="RAG 景点知识",
            evidence=(),
        )


class NoOpUsageGuard:
    pass


def _ambiguous_error() -> LocationServiceError:
    return LocationServiceError(
        "LOCATION_AMBIGUOUS",
        candidates=[
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
        ],
    )


def _duplicate_kuanzhai_error() -> LocationServiceError:
    return LocationServiceError(
        "LOCATION_AMBIGUOUS",
        candidates=[
            LocationCandidate(
                id="chengdu-kuanzhai-1",
                name="宽窄巷子",
                latitude=30.6631,
                longitude=104.0550,
                address="四川省成都市青羊区长顺上街127号",
                city="成都",
                district="青羊区",
                province="四川省",
                provider="fake-location",
            ),
            LocationCandidate(
                id="chengdu-kuanzhai-2",
                name="宽窄巷子",
                latitude=30.6632,
                longitude=104.0551,
                address="四川省成都市青羊区窄巷子",
                city="成都",
                district="青羊区",
                province="四川省",
                provider="fake-location",
            ),
            LocationCandidate(
                id="chengdu-kuanzhai-3",
                name="宽窄巷子",
                latitude=30.6633,
                longitude=104.0552,
                address="四川省成都市青羊区宽巷子",
                city="成都",
                district="青羊区",
                province="四川省",
                provider="fake-location",
            ),
        ],
    )


def test_attraction_ambiguous_selection_preserves_rating_across_collect_requests() -> None:
    application = FakeAttractionSearchApplication(error=_ambiguous_error())
    rag = RecordingRagAnswerer()

    def agent_factory(_initial_profile: TravelProfile) -> SafeTravelAgent:
        return SafeTravelAgent(
            classifier=RuleIntentClassifier(),
            attraction_search_extractor=AttractionSearchQueryExtractor(),
            attraction_search_application=application,
            attraction_search_renderer=FakeAttractionReplyRenderer(),
            rag_v2_knowledge=rag,
        )

    store = ConfirmationStore()
    chat = TravelChatApplication(
        agent_factory=agent_factory,
        usage_guard=NoOpUsageGuard(),
        confirmation_store=store,
    )

    first = chat.collect(
        user_id=None,
        subject="test-subject",
        thread_id="attraction-rating-recovery",
        trip_id=None,
        message="厦门鼓浪屿附近评分最高的景点",
    )

    assert first.error_code == "LOCATION_AMBIGUOUS"

    application.error = None
    second = chat.collect(
        user_id=None,
        subject="test-subject",
        thread_id="attraction-rating-recovery",
        trip_id=None,
        message="鼓浪屿风景名胜区",
    )

    assert second.error_code is None
    assert len(application.requests) == 2
    assert application.requests[1] == AttractionNearbyApplicationRequest(
        location_query="鼓浪屿风景名胜区",
        city="厦门",
        radius=2000,
        sort_by="rating",
    )
    assert rag.calls == []
    assert (
        store.get_attraction_nearby_pending(
            "test-subject", "attraction-rating-recovery", None
        )
        is None
    )


def test_duplicate_location_labels_map_to_confirmed_candidate_and_restore_rating() -> None:
    application = FakeAttractionSearchApplication(error=_duplicate_kuanzhai_error())
    rag = RecordingRagAnswerer()

    def agent_factory(_initial_profile: TravelProfile) -> SafeTravelAgent:
        return SafeTravelAgent(
            classifier=RuleIntentClassifier(),
            attraction_search_extractor=AttractionSearchQueryExtractor(),
            attraction_search_application=application,
            attraction_search_renderer=FakeAttractionReplyRenderer(),
            rag_v2_knowledge=rag,
        )

    chat = TravelChatApplication(
        agent_factory=agent_factory,
        usage_guard=NoOpUsageGuard(),
        confirmation_store=ConfirmationStore(),
    )
    thread_id = "attraction-duplicate-location-rating"
    labels = (
        "宽窄巷子（四川省成都市青羊区长顺上街127号）",
        "宽窄巷子（四川省成都市青羊区窄巷子）",
        "宽窄巷子（四川省成都市青羊区宽巷子）",
    )

    first = chat.collect(
        user_id=None,
        subject="test-subject",
        thread_id=thread_id,
        trip_id=None,
        message="成都宽窄巷子附近评分最高的景点",
    )

    assert first.error_code == "LOCATION_AMBIGUOUS"
    for label in labels:
        assert label in first.reply
    assert "1. 宽窄巷子\n" not in first.reply
    assert "2. 宽窄巷子\n" not in first.reply
    assert "3. 宽窄巷子\n" not in first.reply

    application.error = None
    second = chat.collect(
        user_id=None,
        subject="test-subject",
        thread_id=thread_id,
        trip_id=None,
        message=labels[0],
    )

    assert second.error_code is None
    assert len(application.requests) == 2
    request = application.requests[1]
    assert request.city == "成都"
    assert request.radius == 2000
    assert request.sort_by == "rating"
    confirmed = getattr(request, "resolved_location", None)
    assert confirmed is not None
    assert confirmed.id == "chengdu-kuanzhai-1"
    assert rag.calls == []


def test_numeric_selection_resolves_attraction_location_candidate() -> None:
    application = FakeAttractionSearchApplication(error=_duplicate_kuanzhai_error())
    rag = RecordingRagAnswerer()

    def agent_factory(_initial_profile: TravelProfile) -> SafeTravelAgent:
        return SafeTravelAgent(
            classifier=RuleIntentClassifier(),
            attraction_search_extractor=AttractionSearchQueryExtractor(),
            attraction_search_application=application,
            attraction_search_renderer=FakeAttractionReplyRenderer(),
            rag_v2_knowledge=rag,
        )

    chat = TravelChatApplication(
        agent_factory=agent_factory,
        usage_guard=NoOpUsageGuard(),
        confirmation_store=ConfirmationStore(),
    )
    thread_id = "attraction-numeric-location-selection"

    first = chat.collect(
        user_id=None,
        subject="test-subject",
        thread_id=thread_id,
        trip_id=None,
        message="成都宽窄巷子附近评分最高的景点",
    )

    assert first.error_code == "LOCATION_AMBIGUOUS"
    assert "1. 宽窄巷子" in first.reply
    assert "2. 宽窄巷子" in first.reply
    assert "3. 宽窄巷子" in first.reply

    application.error = None
    second = chat.collect(
        user_id=None,
        subject="test-subject",
        thread_id=thread_id,
        trip_id=None,
        message="1",
    )

    assert second.error_code is None
    assert len(application.requests) == 2
    request = application.requests[1]
    assert request.city == "成都"
    assert request.radius == 2000
    assert request.sort_by == "rating"
    confirmed = getattr(request, "resolved_location", None)
    assert confirmed is not None
    assert confirmed.id == "chengdu-kuanzhai-1"
    assert confirmed.latitude == 30.6631
    assert confirmed.longitude == 104.0550
    assert rag.calls == []


def test_attraction_pending_and_hotel_pending_are_stored_independently() -> None:
    import app.agent.graph as graph

    attraction_pending_type = getattr(
        graph, "PendingAttractionNearbySelection", None
    )
    assert attraction_pending_type is not None

    store = ConfirmationStore()
    hotel_pending = PendingHotelNearbySelection(
        city="厦门",
        radius=2000,
        candidate_names=("厦门大学",),
        sort_by="rating",
    )
    attraction_pending = attraction_pending_type(
        city="厦门",
        radius=2000,
        candidate_names=("鼓浪屿风景名胜区",),
        sort_by="distance",
    )

    store.put_hotel_nearby_pending("subject", "thread", None, hotel_pending)
    store.put_attraction_nearby_pending("subject", "thread", None, attraction_pending)

    assert store.get_hotel_nearby_pending("subject", "thread", None) == hotel_pending
    assert (
        store.get_attraction_nearby_pending("subject", "thread", None)
        == attraction_pending
    )
