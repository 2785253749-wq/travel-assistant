from unittest.mock import Mock

from app.agent.graph import SafeTravelAgent
from app.agent.intent import IntentResult
from app.schemas import TravelProfile


class StubClassifier:
    def __init__(self, intent: str = "plan_trip") -> None:
        self.intent = intent

    def classify(self, message: str, has_trip: bool) -> IntentResult:
        return IntentResult(intent=self.intent, confidence=0.9)


class StubExtractor:
    def extract(self, message: str, profile: TravelProfile) -> TravelProfile:
        return profile


def make_agent(*, intent: str = "plan_trip", profile: TravelProfile | None = None, planner=None):
    return SafeTravelAgent(
        classifier=StubClassifier(intent),
        extractor=StubExtractor(),
        planner=planner or Mock(),
        initial_profile=profile or TravelProfile(origin="上海"),
    )


def test_missing_fields_asks_without_calling_planner():
    planner = Mock()
    result = make_agent(planner=planner).run("从上海出发", trip=None)

    assert result.stage == "collecting"
    assert "目的地" in result.reply
    planner.invoke.assert_not_called()


def test_live_inventory_question_is_refused():
    result = make_agent().run("保证明天还有两张高铁票并帮我买", trip=None)

    assert result.error_code == "UNVERIFIABLE_REALTIME_REQUEST"
    assert "12306" in result.reply


def test_modify_without_trip_routes_to_creation():
    result = make_agent(intent="modify_trip").run("把第二天改成西湖", trip=None)

    assert result.stage == "collecting"
    assert "先告诉我" in result.reply


def test_invalid_profile_is_collected_without_calling_planner():
    planner = Mock()
    result = make_agent(
        profile=TravelProfile(
            origin="上海", destination="杭州", start_date="2026-10-01",
            end_date="2026-10-08", travelers=2, budget_cny=3000,
        ),
        planner=planner,
    ).run("规划行程", trip=None)

    assert result.stage == "collecting"
    assert result.issues[0].code == "trip_duration"
    planner.invoke.assert_not_called()


def test_unverified_planner_response_is_marked_before_returning():
    planner = Mock()
    planner.invoke.return_value = "建议住在市中心，票价是 100 元。"
    result = make_agent(
        profile=TravelProfile(
            origin="上海", destination="杭州", start_date="2026-10-01",
            end_date="2026-10-02", travelers=2, budget_cny=3000,
        ),
        planner=planner,
    ).run("规划行程", trip=None)

    assert result.stage == "planned"
    assert "待确认" in result.reply


def test_agent_error_does_not_expose_internal_details():
    planner = Mock()
    planner.invoke.side_effect = RuntimeError("postgres password=secret")
    result = make_agent(
        profile=TravelProfile(
            origin="上海", destination="杭州", start_date="2026-10-01",
            end_date="2026-10-02", travelers=2, budget_cny=3000,
        ),
        planner=planner,
    ).run("规划行程", trip=None)

    assert result.error_code == "AGENT_UNAVAILABLE"
    assert "secret" not in result.reply
