import copy
import json
from dataclasses import dataclass
from datetime import datetime, timezone

import pytest

from app.agent.graph import (
    ModelStructuredPlanner,
    SafeTravelAgent,
    TrustedEvidence,
)
from app.agent.intent import IntentResult
from app.schemas import TravelProfile


class _PlanClassifier:
    def classify(self, message: str, has_trip: bool) -> IntentResult:
        return IntentResult(intent="plan_trip", confidence=1.0)


class _ProfileExtractor:
    def extract(self, message: str, profile: TravelProfile) -> TravelProfile:
        return profile


class _EvidenceProvider:
    def __init__(self, fetched_at: datetime) -> None:
        self._evidence = [
            TrustedEvidence(
                "ev-hangzhou",
                "West Lake is in Hangzhou.",
                "https://www.hangzhou.gov.cn/",
                "official",
                fetched_at,
            )
        ]

    def fetch(self, profile: TravelProfile) -> list[TrustedEvidence]:
        return self._evidence


@dataclass
class _RawModelResponse:
    content: object


class _SequenceChatModel:
    """Minimal production-boundary fake: only the raw invoke API exists."""

    def __init__(self, candidates: list[object]) -> None:
        self._candidates = iter(candidates)
        self.messages: list[object] = []

    def invoke(self, messages: object) -> _RawModelResponse:
        self.messages.append(messages)
        candidate = next(self._candidates)
        content = candidate if isinstance(candidate, str) else json.dumps(candidate)
        return _RawModelResponse(content)


def _profile() -> TravelProfile:
    return TravelProfile(
        origin="北京",
        destination="杭州",
        start_date="2026-10-01",
        end_date="2026-10-02",
        travelers=2,
        budget_cny=3000,
    )


def _valid_candidate() -> dict[str, object]:
    return {
        "title": "model title",
        "start_date": "2026-10-01",
        "end_date": "2026-10-02",
        "days": [
            {
                "date": "2026-10-01",
                "morning": {"title": "Walk", "start_time": "09:00", "end_time": "11:00"},
                "afternoon": {"title": "Museum", "start_time": "13:00", "end_time": "15:00"},
                "evening": {"title": "Dinner", "start_time": "18:00", "end_time": "20:00"},
            },
            {
                "date": "2026-10-02",
                "morning": {"title": "Park", "start_time": "09:00", "end_time": "11:00"},
                "afternoon": {"title": "Market", "start_time": "13:00", "end_time": "15:00"},
                "evening": {"title": "Return", "start_time": "17:00", "end_time": "19:00"},
            },
        ],
        "budget": {
            "transport": 800,
            "hotel": 1000,
            "food": 700,
            "tickets": 200,
            "reserve": 300,
            "other": 0,
            "total": 3000,
            "trip_total": 3000,
            "currency": "CNY",
            "traveler_basis": "trip_total",
            "traveler_count": 2,
            "estimate": {
                "low": 2800,
                "point": 3000,
                "high": 3200,
                "currency": "CNY",
                "basis": "trip_total",
                "assumption_id": "cost-v1",
            },
        },
        "notes": [],
        "assumptions": [
            {
                "assumption_id": "cost-v1",
                "category": "budget",
                "description": "Offline planning estimate.",
            }
        ],
    }


def _run_production_planner(monkeypatch: pytest.MonkeyPatch, candidates: list[object]):
    now = datetime.now(timezone.utc)
    chat_model = _SequenceChatModel(candidates)
    monkeypatch.setattr("app.agent.graph.model", lambda: chat_model)
    agent = SafeTravelAgent(
        classifier=_PlanClassifier(),
        extractor=_ProfileExtractor(),
        planner=ModelStructuredPlanner(),
        evidence_provider=_EvidenceProvider(now),
        initial_profile=_profile(),
    )
    return agent.run("规划行程", trip=None), chat_model


def test_production_model_seam_sanitizes_malicious_display_fields_before_schema_validation(monkeypatch):
    candidate = _valid_candidate()
    candidate.pop("title")
    candidate["notes"] = {"claim": "Hotel cost is CNY 399"}
    days = candidate["days"]
    days[0]["morning"].pop("title")
    days[0]["morning"].pop("notes", None)
    days[0]["afternoon"]["title"] = ""
    days[0]["afternoon"]["notes"] = "All rooms are sold out"
    days[0]["evening"]["title"] = "x" * 301
    days[0]["evening"]["notes"] = ["Hotel cost is CNY 399"]
    days[1]["morning"]["title"] = {"claim": "All rooms are sold out"}
    days[1]["morning"]["notes"] = 399

    result, chat_model = _run_production_planner(monkeypatch, [candidate])

    assert result.stage == "planned"
    assert result.error_code is None
    assert result.itinerary is not None
    assert not result.reply.lstrip().startswith("{")
    itinerary = result.itinerary.model_dump(mode="json")
    assert itinerary["title"] == "杭州 | 2-day itinerary"
    assert itinerary["notes"] == []
    assert [
        (activity["title"], activity["notes"])
        for day in itinerary["days"]
        for activity in (day["morning"], day["afternoon"], day["evening"])
    ] == [
        ("Day 1 morning", []),
        ("Day 1 afternoon", []),
        ("Day 1 evening", []),
        ("Day 2 morning", []),
        ("Market", []),
        ("Return", []),
    ]
    assert len(chat_model.messages) == 1


def test_production_model_seam_repairs_one_non_display_error(monkeypatch):
    invalid = _valid_candidate()
    invalid["budget"]["total"] = 2999

    result, chat_model = _run_production_planner(monkeypatch, [invalid, _valid_candidate()])

    assert result.stage == "planned"
    assert result.error_code is None
    assert len(chat_model.messages) == 2
    second_request = json.loads(chat_model.messages[1][1].content)
    assert second_request["repair_codes"] == ["SCHEMA_INVALID"]


def _non_display_structure_error() -> dict[str, object]:
    candidate = _valid_candidate()
    candidate["budget"] = {"total": "not-a-budget"}
    return candidate


@pytest.mark.parametrize(
    "invalid_candidate",
    ["{malformed-json", ["not", "a", "mapping"], _non_display_structure_error()],
    ids=["malformed-json", "non-mapping", "non-display-structure"],
)
def test_production_model_seam_fails_closed_after_one_repair(monkeypatch, invalid_candidate):
    result, chat_model = _run_production_planner(
        monkeypatch,
        [copy.deepcopy(invalid_candidate), copy.deepcopy(invalid_candidate)],
    )

    assert result.stage == "collecting"
    assert result.error_code == "PLAN_VALIDATION_FAILED"
    assert len(chat_model.messages) == 2
