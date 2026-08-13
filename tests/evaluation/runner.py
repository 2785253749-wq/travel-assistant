"""Deterministic, offline regression evaluation for the public MVP.

Ordinary journeys drive ``SafeTravelAgent`` while exception cases exercise the
named production component directly. Fixed seams prevent network or paid-model
calls.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from time import perf_counter
from types import SimpleNamespace
from typing import Any, Literal
from uuid import UUID, uuid4

import httpx

from app.agent.graph import (
    ChatResult,
    RuleIntentClassifier,
    RuleTravelExtractor,
    SafeTravelAgent,
    TrustedEvidence,
    extract_profile,
)
from app.application.chat import ConfirmationStore, TravelChatApplication
from app.agent.extraction import ExtractionCandidate
from app.agent.intent import IntentResult, classify_intent
from app.agent.planning import PlanValidationError, Planner
from app.core.errors import AppError
from app.core.usage import InMemoryUsageRepository, ModelGateway, ProviderCircuitBreaker, ProviderUnavailable, UsageGuard
from app.infrastructure.repositories import InMemoryTripRepository
from app.trips.models import ConversationMessage, Trip
from app.trips.service import TripService
from app.schemas import Itinerary, TravelProfile
from app.providers.free_weather import WeatherProvider
from app.providers.places import PlacesProvider
from app.application.weather import WeatherService
from app.providers.amap_weather import (
    AMAP_WEATHER_SOURCE,
    AmapForecast,
    AmapForecastCast,
    AmapWeatherPayload,
)
from app.providers.base import ProviderResult
from app.rag.embedding import EMBEDDING_DIMENSIONS
from app.rag.models import RetrievedChunk
from app.rag.service import KnowledgeAnswerService
from tests.evaluation.offline_fixtures import OfflineModel, SCENARIO_BY_MESSAGE, model_factory, source_ids_for


ACTION = Literal["ask", "refuse", "plan", "modify", "explain", "degrade"]
FIXTURE_NOW = datetime(2026, 7, 28, tzinfo=timezone.utc)
OFFLINE_COMPONENT_HARNESS_VERSION = "offline-components-v2"
PRODUCTION_FLOW_HARNESS_VERSION = "production-flow-v2"
RAG_WEATHER_HARNESS_VERSION = "rag-weather-offline-v1"
REQUIRED_RAG_WEATHER_METRICS = {
    "grounded_source_rate": 1.0,
    "refusal_accuracy": 1.0,
    "citation_completeness": 1.0,
    "weather_boundary_accuracy": 1.0,
}
RAG_WEATHER_CASES_PATH = Path("tests/evaluation/rag_weather_cases.jsonl")


@dataclass(frozen=True)
class EvaluationCase:
    id: str
    category: str
    messages: list[str]
    expected_intent: str
    expected_fields: dict[str, Any]
    expected_action: ACTION
    allowed_sources: list[str]
    expected_error: str | None = None
    fixture_profiles: list[dict[str, Any]] = field(default_factory=list)
    fixture_intent: str | None = None
    has_trip: bool = False
    provider_scenario: str | None = None
    expect_schema: bool = False
    slot_applicable: bool = True

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "EvaluationCase":
        return cls(**value)


@dataclass(frozen=True)
class Prediction:
    intent: str
    action: str
    fields: dict[str, Any]
    error_code: str | None = None
    schema_valid: bool = False
    budget_valid: bool = False
    citation_ids: list[str] = field(default_factory=list)
    unsupported_facts: int = 0
    fallback_safe: bool = False


@dataclass(frozen=True)
class ScenarioObservation:
    component: str
    intent: str
    action: ACTION
    fields: dict[str, Any]
    error_code: str | None = None
    schema_valid: bool = False
    budget_valid: bool = False
    citation_ids: list[str] = field(default_factory=list)
    unsupported_facts: int = 0
    fallback_safe: bool = False
    attempts: int = 1

    def to_prediction(self) -> Prediction:
        return Prediction(
            intent=self.intent,
            action=self.action,
            fields=self.fields,
            error_code=self.error_code,
            schema_valid=self.schema_valid,
            budget_valid=self.budget_valid,
            citation_ids=self.citation_ids,
            unsupported_facts=self.unsupported_facts,
            fallback_safe=self.fallback_safe,
        )


@dataclass(frozen=True)
class EvaluationReport:
    total_cases: int
    intent_accuracy: float
    slot_micro_f1: float
    clarification_recall: float
    refusal_precision: float
    refusal_recall: float
    schema_validity: float
    budget_validity: float
    citation_coverage: float
    citation_validity: float
    unsupported_fact_rate: float
    task_success_rate: float
    fallback_success_rate: float
    overall: float
    denominators: dict[str, int]
    failures: dict[str, list[str]]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RagWeatherCase:
    id: str
    category: Literal["grounded", "refusal", "citation_safety", "weather_boundary"]
    question: str
    allowed_sources: list[str]
    region: str | None = None
    expected_status: Literal["grounded", "refused"] | None = None
    expected_topic: str | None = None
    expected_evidence: str | None = None
    city_id: str | None = None
    day_offset: int | None = None
    expected_weather_status: Literal["available", "unavailable"] | None = None

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "RagWeatherCase":
        return cls(**value)


@dataclass(frozen=True)
class RagWeatherPrediction:
    status: str | None = None
    source_labels: tuple[str, ...] = ()
    topics: tuple[str, ...] = ()
    evidence: tuple[str, ...] = ()
    citation_complete: bool = False
    weather_status: str | None = None


@dataclass(frozen=True)
class RagWeatherEvaluationReport:
    total_cases: int
    grounded_source_rate: float
    refusal_accuracy: float
    citation_completeness: float
    weather_boundary_accuracy: float
    denominators: dict[str, int]
    failures: dict[str, list[str]]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ProductionFlowStep:
    name: str
    success: bool
    latency_ms: float
    failure_type: str | None = None


@dataclass(frozen=True)
class ProductionCompositionReport:
    mode: str
    harness_version: str
    production_components: tuple[str, ...]
    seam_disclosure: str
    change_summary: tuple[str, ...]
    steps: tuple[ProductionFlowStep, ...]
    success_rate: float
    p50_latency_ms: float
    p95_latency_ms: float
    model_calls: int
    input_tokens: int
    output_tokens: int
    estimated_cost_micros: int
    cost_basis: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class OfflineClassifier:
    """Exercise Task 2's JSON model gateway and deterministic route."""

    def __init__(self) -> None:
        self.last_intent = "unsupported"

    def classify(self, message: str, has_trip: bool) -> IntentResult:
        # Retain the fixture's request label for reporting if the real gateway
        # raises before a structured response exists.
        self.last_intent = OfflineModel.intent_for(message)
        result = classify_intent(message, has_trip, model=OfflineModel())
        self.last_intent = result.intent
        return result


class OfflineExtractor:
    """Exercise Task 2's extraction prompt and ModelGateway seam."""

    def __init__(self) -> None:
        self.last_invalid_fields: dict[str, int] = {}

    def begin_message(self) -> None:
        self.last_invalid_fields = {}

    def extract(self, message: str, profile: TravelProfile) -> ExtractionCandidate:
        extraction = extract_profile(message, profile, model_factory=model_factory)
        self.last_invalid_fields = extraction.invalid_fields
        return extraction


class FixtureEvidenceProvider:
    def __init__(self, source_ids: list[str]) -> None:
        self._source_ids = source_ids

    def use_message(self, message: str) -> None:
        self._source_ids = source_ids_for(message)

    def fetch(self, profile: TravelProfile) -> list[TrustedEvidence]:
        return [
            TrustedEvidence(
                source_id,
                f"{profile.destination} travel reference {index + 1}",
                f"https://www.gov.cn/evaluation/{source_id}",
                "official",
                FIXTURE_NOW,
            )
            for index, source_id in enumerate(self._source_ids)
        ]


class FixtureStructuredPlanner:
    def __init__(self, scenario: str | None) -> None:
        self._scenario = scenario
        self._planner = Planner(self._generate, now=lambda: FIXTURE_NOW)
        self.attempts = 0
        self.revisions: list[tuple[Itinerary, str, str]] = []

    def plan(self, profile: TravelProfile, provider_results: object) -> Itinerary:
        return self._planner.plan(profile, provider_results)

    def revise(
        self,
        profile: TravelProfile,
        provider_results: object,
        *,
        itinerary: Itinerary,
        instruction: str,
    ) -> Itinerary:
        payload = _fixture_itinerary(profile, provider_results)
        marker = hashlib.sha256(
            (itinerary.model_dump_json() + "\0" + instruction).encode("utf-8")
        ).hexdigest()[:12]
        payload["title"] = f"fixture-revision-{marker}"
        self.revisions.append((itinerary, instruction, marker))
        return Planner(lambda *_: payload, now=lambda: FIXTURE_NOW).plan(
            profile, provider_results
        )

    def _generate(self, profile: TravelProfile, provider_results: object, repair_codes: list[str] | None) -> object:
        self.attempts += 1
        if self._scenario == "format_twice":
            return {"invalid": True}
        return _fixture_itinerary(profile, provider_results)


def _fixture_itinerary(profile: TravelProfile, provider_results: object) -> dict[str, Any]:
    assert profile.start_date and profile.end_date and profile.destination and profile.travelers
    start, end = date.fromisoformat(profile.start_date), date.fromisoformat(profile.end_date)
    evidence = tuple(provider_results) if isinstance(provider_results, tuple) else ()
    first_fact = [] if not evidence else [{"text": evidence[0].fact, "evidence_id": evidence[0].evidence_id}]
    days: list[dict[str, Any]] = []
    for offset in range((end - start).days + 1):
        days.append({
            "date": (date.fromordinal(start.toordinal() + offset)).isoformat(),
            "morning": {"title": "fixture", "start_time": "09:00", "end_time": "11:00", "notes": [], "facts": first_fact if offset == 0 else [], "citations": []},
            "afternoon": {"title": "fixture", "start_time": "13:00", "end_time": "16:00", "notes": [], "facts": [], "citations": []},
            "evening": {"title": "fixture", "start_time": "18:00", "end_time": "20:00", "notes": [], "facts": [], "citations": []},
        })
    total = profile.budget_cny or 2000
    return {
        "title": "fixture", "start_date": start.isoformat(), "end_date": end.isoformat(), "days": days,
        "budget": {"transport": total // 4, "hotel": total // 3, "food": total // 5, "tickets": total // 10, "reserve": total - (total // 4 + total // 3 + total // 5 + total // 10), "other": 0, "total": total, "currency": "CNY", "traveler_basis": "trip_total", "traveler_count": profile.travelers, "trip_total": total, "estimate": {"low": total - 100, "point": total, "high": total + 100, "currency": "CNY", "basis": "trip_total", "assumption_id": "eval-budget"}},
        "notes": [], "assumptions": [{"assumption_id": "eval-budget", "category": "budget", "description": "Offline evaluation budget estimate."}], "citations": [],
    }


def load_cases(path: str | Path) -> list[EvaluationCase]:
    allowed_keys = {"id", "category", "messages", "expected_intent", "expected_fields", "expected_action", "allowed_sources", "expected_error", "has_trip", "expect_schema", "slot_applicable"}
    raw_cases = [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]
    for raw_case in raw_cases:
        unknown = set(raw_case) - allowed_keys
        if unknown or any(key.startswith("fixture_") for key in raw_case) or "provider_scenario" in raw_case:
            raise ValueError(f"evaluation case contains forbidden fixture fields: {sorted(unknown)}")
    cases = [EvaluationCase.from_dict(value) for value in raw_cases]
    expected_ids = [f"P{i:03d}" for i in range(1, 21)] + [f"M{i:03d}" for i in range(1, 21)] + [f"R{i:03d}" for i in range(1, 16)] + [f"N{i:03d}" for i in range(1, 16)] + [f"E{i:03d}" for i in range(1, 11)]
    if len(cases) != 80 or [case.id for case in cases] != expected_ids:
        raise ValueError("cases.jsonl must contain exactly ordered P001-P020, M001-M020, R001-R015, N001-N015, E001-E010")
    if len({case.id for case in cases}) != 80:
        raise ValueError("evaluation case IDs must be unique")
    if any(case.expected_action not in {"ask", "refuse", "plan", "modify", "explain", "degrade"} for case in cases):
        raise ValueError("evaluation expected_action is invalid")
    return cases


def load_rag_weather_cases(
    path: str | Path = RAG_WEATHER_CASES_PATH,
) -> list[RagWeatherCase]:
    raw_cases = [
        json.loads(line)
        for line in Path(path).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    cases = [RagWeatherCase.from_dict(value) for value in raw_cases]
    expected_ids = (
        [f"G{i:03d}" for i in range(1, 46)]
        + [f"RG{i:03d}" for i in range(1, 16)]
        + [f"C{i:03d}" for i in range(1, 11)]
        + [f"W{i:03d}" for i in range(1, 11)]
    )
    if len(cases) != 80 or [case.id for case in cases] != expected_ids:
        raise ValueError("RAG/weather cases must contain the stable G, RG, C and W IDs")
    if len({case.id for case in cases}) != 80:
        raise ValueError("RAG/weather evaluation case IDs must be unique")
    _validate_rag_weather_cases(cases)
    return cases


def _validate_rag_weather_cases(cases: list[RagWeatherCase]) -> None:
    expected_counts = {
        "grounded": 45,
        "refusal": 15,
        "citation_safety": 10,
        "weather_boundary": 10,
    }
    actual_counts = {
        category: sum(case.category == category for case in cases)
        for category in expected_counts
    }
    if actual_counts != expected_counts:
        raise ValueError("RAG/weather evaluation category distribution is invalid")
    for case in cases:
        expected_status = (
            "grounded"
            if case.category in {"grounded", "citation_safety"}
            else "refused" if case.category == "refusal" else None
        )
        if case.expected_status != expected_status:
            raise ValueError(f"RAG/weather case {case.id} has inconsistent expected_status")
        if case.category in {"grounded", "citation_safety"} and (
            not case.expected_topic or not case.expected_evidence
        ):
            raise ValueError(f"RAG/weather case {case.id} requires topic and evidence")


def load_baseline(path: str | Path = "tests/evaluation/baseline.json") -> dict[str, Any]:
    baseline = json.loads(Path(path).read_text(encoding="utf-8"))
    if set(baseline) < {"version", "thresholds", "known_failures"}:
        raise ValueError("baseline requires version, thresholds and known_failures")
    if not isinstance(baseline["thresholds"], dict) or not isinstance(baseline["known_failures"], list):
        raise ValueError("baseline has invalid gate schema")
    return baseline


def run_case(case: EvaluationCase) -> Prediction:
    scenario = SCENARIO_BY_MESSAGE.get(case.messages[-1])
    if scenario is not None:
        return observe_scenario(case.messages[-1]).to_prediction()
    classifier = OfflineClassifier()
    extractor = OfflineExtractor()
    evidence_provider = FixtureEvidenceProvider([])
    agent = SafeTravelAgent(
        classifier=classifier,
        extractor=extractor,
        planner=FixtureStructuredPlanner(scenario),
        evidence_provider=evidence_provider,
    )
    trip = None
    result: ChatResult | None = None
    try:
        for index, message in enumerate(case.messages):
            evidence_provider.use_message(message)
            extractor.begin_message()
            result = agent.run(message, trip=trip)
            if index < len(case.messages) - 1 and result.profile:
                profile = TravelProfile.model_validate(result.profile)
                if trip is None:
                    trip = SimpleNamespace(profile=profile, id=uuid4())
                else:
                    trip.profile = profile
    except ProviderUnavailable as error:
        return Prediction(intent=classifier.last_intent, action="degrade", fields={}, error_code=error.code, fallback_safe=True)
    assert result is not None
    fields = {**result.profile, **extractor.last_invalid_fields}
    schema_valid, budget_valid, citation_ids = _structured_output(result)
    predicted_action = _action_for(result, classifier.last_intent, has_trip=trip is not None)
    unsupported_facts = _unsupported_fact_count(result)
    return Prediction(
        intent=classifier.last_intent, action=predicted_action, fields=fields, error_code=result.error_code,
        schema_valid=schema_valid, budget_valid=budget_valid, citation_ids=citation_ids,
        unsupported_facts=unsupported_facts, fallback_safe=predicted_action == "degrade" and bool(result.error_code),
    )


def observe_scenario(message: str) -> ScenarioObservation:
    """Run the raw message's target failure seam and return only its observation."""
    scenario = SCENARIO_BY_MESSAGE.get(message)
    if scenario == "weather_timeout":
        return _observe_weather_timeout(message)
    if scenario == "places_empty_retry":
        return _observe_places_empty_retry(message)
    if scenario in {"user_limit", "global_limit", "kill_switch"}:
        return _observe_usage_guard(message, scenario)
    if scenario in {"circuit_open", "model_rate_limited", "model_upstream_failure"}:
        return _observe_model_gateway(message, scenario)
    if scenario == "format_twice":
        return _observe_planner(message)
    if scenario == "database_failure":
        return _observe_database_failure(message)
    raise ValueError(f"raw message has no evaluation scenario: {message}")


def _scenario_profile(message: str) -> TravelProfile:
    return TravelProfile.model_validate(OfflineModel.profile_for(message))


def _observation(
    message: str,
    component: str,
    action: ACTION,
    error_code: str | None,
    *,
    fields: dict[str, Any] | None = None,
    fallback_safe: bool = False,
    attempts: int = 1,
) -> ScenarioObservation:
    return ScenarioObservation(
        component=component,
        intent=OfflineModel.intent_for(message),
        action=action,
        fields=fields if fields is not None else _scenario_profile(message).model_dump(),
        error_code=error_code,
        fallback_safe=fallback_safe,
        attempts=attempts,
    )


def _observe_weather_timeout(message: str) -> ScenarioObservation:
    profile = _scenario_profile(message)

    def timeout(_: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("fixture timeout")

    result = WeatherProvider(client=httpx.Client(transport=httpx.MockTransport(timeout))).forecast(
        profile.destination or "",
        date.fromisoformat(profile.start_date or ""),
        date.fromisoformat(profile.end_date or ""),
    )
    action: ACTION = "degrade" if result.degraded else "plan"
    return _observation(
        message,
        "weather_provider",
        action,
        result.error_code,
        fallback_safe=result.degraded,
    )


def _observe_places_empty_retry(message: str) -> ScenarioObservation:
    attempts = 0

    def empty(_: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(200, json={"features": []})

    profile = _scenario_profile(message)
    result = PlacesProvider(client=httpx.Client(transport=httpx.MockTransport(empty))).search(
        profile.destination or "",
        "fixture scenic area",
    )
    action: ACTION = "degrade" if result.degraded else "plan"
    return _observation(
        message,
        "places_provider",
        action,
        result.error_code,
        fallback_safe=result.degraded,
        attempts=attempts,
    )


def _observe_usage_guard(message: str, scenario: str) -> ScenarioObservation:
    repository = InMemoryUsageRepository()
    user_limit, global_limit = {
        "user_limit": (0, 10),
        "global_limit": (10, 0),
        "kill_switch": (10, 10),
    }[scenario]
    guard = UsageGuard(
        repository=repository,
        user_daily_limit=user_limit,
        global_daily_limit=global_limit,
        enabled=scenario != "kill_switch",
        clock=lambda: FIXTURE_NOW,
    )
    try:
        guard.reserve("evaluation-subject")
    except AppError as error:
        return _observation(message, "usage_guard", "ask", error.code)
    raise AssertionError(f"usage scenario unexpectedly reserved: {scenario}")


def _observe_model_gateway(message: str, scenario: str) -> ScenarioObservation:
    breaker = ProviderCircuitBreaker(failure_threshold=1)
    if scenario == "circuit_open":
        breaker.record_failure("AI_PROVIDER_UNAVAILABLE")
    gateway = ModelGateway(lambda: OfflineModel(), breaker)
    try:
        gateway.invoke([SimpleNamespace(content=message)])
    except ProviderUnavailable as error:
        return _observation(
            message,
            "model_gateway",
            "degrade",
            error.code,
            fallback_safe=True,
        )
    raise AssertionError(f"model scenario unexpectedly succeeded: {scenario}")


def _observe_planner(message: str) -> ScenarioObservation:
    profile = _scenario_profile(message)
    planner = FixtureStructuredPlanner("format_twice")
    try:
        planner.plan(profile, ())
    except PlanValidationError as error:
        return _observation(
            message,
            "planner",
            "degrade",
            error.code,
            fallback_safe=True,
            attempts=planner.attempts,
        )
    raise AssertionError("twice-invalid planner scenario unexpectedly succeeded")


class _FailingMessageRepository(InMemoryTripRepository):
    def append_message(self, message: ConversationMessage) -> None:
        raise RuntimeError("offline database failure")


def _observe_database_failure(message: str) -> ScenarioObservation:
    user_id = uuid4()
    profile = _scenario_profile(message)
    repository = _FailingMessageRepository()
    trip = repository.create(Trip(user_id=user_id, title="evaluation trip", profile=profile))
    result = SafeTravelAgent(
        classifier=OfflineClassifier(),
        extractor=OfflineExtractor(),
        evidence_provider=FixtureEvidenceProvider([]),
        repository=TripService(repository),
    ).run(message, trip=trip, user_id=user_id)
    action = _action_for(result, OfflineModel.intent_for(message), has_trip=True)
    schema_valid, budget_valid, citation_ids = _structured_output(result)
    return ScenarioObservation(
        component="safe_travel_agent",
        intent=OfflineModel.intent_for(message),
        action=action,
        fields=result.profile,
        error_code=result.error_code,
        schema_valid=schema_valid,
        budget_valid=budget_valid,
        citation_ids=citation_ids,
        unsupported_facts=_unsupported_fact_count(result),
        fallback_safe=action == "degrade" and result.error_code is not None,
    )


def _structured_output(result: ChatResult) -> tuple[bool, bool, list[str]]:
    try:
        itinerary = (
            Itinerary.model_validate(result.itinerary)
            if result.itinerary is not None
            else Itinerary.model_validate_json(result.reply)
        )
    except Exception:
        return False, False, []
    citations = [citation.evidence_id for citation in itinerary.citations]
    for day in itinerary.days:
        for activity in (day.morning, day.afternoon, day.evening):
            citations.extend(citation.evidence_id for citation in activity.citations)
    profile = TravelProfile.model_validate(result.profile)
    budget = itinerary.budget
    categories = budget.transport + budget.hotel + budget.food + budget.tickets + budget.reserve + budget.other
    trip_total = budget.total if budget.traveler_basis == "trip_total" else budget.total * budget.traveler_count
    range_valid = budget.estimate.low <= budget.estimate.point <= budget.estimate.high and budget.estimate.point == budget.total
    cap_valid = profile.budget_cny is None or trip_total <= profile.budget_cny
    return True, categories == budget.total and trip_total == budget.trip_total and range_valid and cap_valid, citations


def _unsupported_fact_count(result: ChatResult) -> int:
    """Count factual activity claims that do not carry a valid citation.

    Safe fallbacks have neither a structured fact nor a factual-text marker and
    therefore remain zero, rather than being treated as hallucinations.
    """
    if result.error_code in {"UNVERIFIABLE_REALTIME_REQUEST", "OUT_OF_SCOPE", "HIGH_STAKES_ADVICE"}:
        return 0
    try:
        itinerary = (
            Itinerary.model_validate(result.itinerary)
            if result.itinerary is not None
            else Itinerary.model_validate_json(result.reply)
        )
    except Exception:
        text = result.reply.lower()
        return int(any(marker in text for marker in ("实时价格", "余票", "库存", "price is", "sold out")) and not result.sources)
    unsupported = 0
    for day in itinerary.days:
        for activity in (day.morning, day.afternoon, day.evening):
            cited = {citation.evidence_id for citation in activity.citations}
            unsupported += sum(claim.evidence_id not in cited for claim in activity.facts)
    return unsupported


def _action_for(result: ChatResult, intent: str, *, has_trip: bool) -> str:
    if result.error_code in {"UNVERIFIABLE_REALTIME_REQUEST", "OUT_OF_SCOPE", "HIGH_STAKES_ADVICE"}:
        return "refuse"
    if result.error_code in {"UNVERIFIED_FACTS", "PLAN_VALIDATION_FAILED", "AGENT_UNAVAILABLE", "DESTINATION_UNDETERMINED"}:
        return "degrade" if result.error_code in {"UNVERIFIED_FACTS", "PLAN_VALIDATION_FAILED", "AGENT_UNAVAILABLE"} else "ask"
    if result.stage == "collecting":
        return "ask"
    if intent == "modify_trip" and has_trip:
        return "modify"
    if intent == "explain_trip" and has_trip:
        return "explain"
    return "plan"


def score(predictions: list[Prediction], cases: list[EvaluationCase]) -> EvaluationReport:
    if len(predictions) != len(cases):
        raise ValueError("predictions and cases must have the same length")
    total = len(cases)
    intent_correct = sum(pred.intent == case.expected_intent for pred, case in zip(predictions, cases))
    tp = fp = fn = 0
    slot_cases = 0
    for prediction, case in zip(predictions, cases):
        if not case.slot_applicable:
            continue
        expected = case.expected_fields
        if not expected:
            continue
        slot_cases += 1
        actual = {key: value for key, value in prediction.fields.items() if value not in (None, "", [])}
        for key, value in actual.items():
            if key in expected and expected[key] == value:
                tp += 1
            elif key not in {"preferences", "constraints"}:
                fp += 1
        fn += sum(key not in actual or actual[key] != value for key, value in expected.items())
    slot_f1 = _ratio(2 * tp, 2 * tp + fp + fn, empty=1.0)
    ask_cases = [(pred, case) for pred, case in zip(predictions, cases) if case.expected_action == "ask"]
    refusal_cases = [(pred, case) for pred, case in zip(predictions, cases) if case.expected_action == "refuse"]
    predicted_refusals = [pred for pred in predictions if pred.action == "refuse"]
    schema_cases = [(pred, case) for pred, case in zip(predictions, cases) if case.expect_schema]
    citation_required = [(pred, case) for pred, case in zip(predictions, cases) if case.allowed_sources and case.expect_schema]
    actual_citations = [(citation, case) for pred, case in zip(predictions, cases) for citation in pred.citation_ids]
    fallback_cases = [(pred, case) for pred, case in zip(predictions, cases) if case.expected_action == "degrade"]
    task_successes: list[bool] = []
    failures: dict[str, list[str]] = {}
    for prediction, case in zip(predictions, cases):
        reasons: list[str] = []
        if prediction.action != case.expected_action:
            reasons.append(f"action: expected {case.expected_action}, got {prediction.action}")
        if prediction.intent != case.expected_intent:
            reasons.append(f"intent: expected {case.expected_intent}, got {prediction.intent}")
        if case.expected_error is not None and case.expected_error != prediction.error_code:
            reasons.append(f"error_code: expected {case.expected_error}, got {prediction.error_code}")
        expected_slots = case.expected_fields
        actual_slots = {key: value for key, value in prediction.fields.items() if value not in (None, "", [])}
        if case.slot_applicable and any(actual_slots.get(key) != value for key, value in expected_slots.items()):
            reasons.append("slot: expected fields mismatch")
        if case.expect_schema and not prediction.schema_valid:
            reasons.append("schema: invalid")
        if case.expect_schema and not prediction.budget_valid:
            reasons.append("budget: invalid")
        if case.allowed_sources and case.expect_schema and not set(case.allowed_sources).issubset(prediction.citation_ids):
            reasons.append("citation_coverage: missing allowed source")
        if any(citation not in case.allowed_sources for citation in prediction.citation_ids):
            reasons.append("citation_validity: unexpected source")
        if prediction.unsupported_facts:
            reasons.append("unsupported_fact: uncited claim")
        if case.expected_action == "degrade" and not prediction.fallback_safe:
            reasons.append("fallback: unsafe")
        task_successes.append(not reasons)
        if reasons:
            failures[case.id] = reasons
    metrics = [
        _ratio(intent_correct, total), slot_f1,
        _ratio(sum(pred.action == "ask" for pred, _ in ask_cases), len(ask_cases), empty=1.0),
        _ratio(sum(pred.action == "refuse" for pred, _ in refusal_cases), len(refusal_cases), empty=1.0),
        _ratio(sum(pred.action == "refuse" and case.expected_action == "refuse" for pred, case in zip(predictions, cases)), len(predicted_refusals), empty=1.0),
        _ratio(sum(pred.schema_valid for pred, _ in schema_cases), len(schema_cases), empty=1.0),
        _ratio(sum(pred.budget_valid for pred, _ in schema_cases), len(schema_cases), empty=1.0),
        _ratio(sum(set(case.allowed_sources).issubset(set(pred.citation_ids)) for pred, case in citation_required), len(citation_required), empty=1.0),
        _ratio(sum(citation in case.allowed_sources for citation, case in actual_citations), len(actual_citations), empty=1.0),
        _ratio(sum(pred.fallback_safe for pred, _ in fallback_cases), len(fallback_cases), empty=1.0),
        _ratio(sum(task_successes), total),
    ]
    fact_items = sum(len(pred.citation_ids) + pred.unsupported_facts for pred in predictions)
    return EvaluationReport(
        total, metrics[0], slot_f1, metrics[2], metrics[4], metrics[3], metrics[5], metrics[6], metrics[7], metrics[8],
        _ratio(sum(pred.unsupported_facts for pred in predictions), fact_items, empty=0.0), metrics[10], metrics[9], sum(metrics) / len(metrics),
        {"cases": total, "slot_cases": slot_cases, "slots": 2 * tp + fp + fn, "clarification_cases": len(ask_cases), "required_refusals": len(refusal_cases), "predicted_refusals": len(predicted_refusals), "refusal_true_positives": sum(pred.action == "refuse" and case.expected_action == "refuse" for pred, case in zip(predictions, cases)), "schema_cases": len(schema_cases), "citation_required_cases": len(citation_required), "observed_citations": len(actual_citations), "fallback_cases": len(fallback_cases), "fact_items": fact_items}, failures,
    )


def _ratio(numerator: int, denominator: int, *, empty: float = 0.0) -> float:
    return numerator / denominator if denominator else empty


def evaluate(cases: list[EvaluationCase]) -> EvaluationReport:
    return score([run_case(case) for case in cases], cases)


def run_evaluation(cases: list[EvaluationCase], agent: Any | None = None) -> EvaluationReport:
    """Public interface. ``agent`` is reserved for explicit custom harnesses."""
    if agent is not None:
        predictions = [agent(case) for case in cases]
        return score(predictions, cases)
    return evaluate(cases)


_RAG_TOPICS = {
    "景点": ("山海景点", "自然景点", "海岛景点"),
    "交通": ("省内交通", "省内交通", "公共交通"),
    "美食": ("特色美食", "特色美食", "本地小吃"),
    "季节": ("雨季出行", "雨季出行", "夏季出行"),
    "避坑": ("旅行避坑", "高原注意", "鼓浪屿安排"),
}
_RAG_REGIONS = {
    1: ("福建", "福建文旅试点资料"),
    2: ("云南", "云南文旅试点资料"),
    3: ("厦门", "厦门出行试点资料"),
}
_RAG_TOPIC_MARKERS = {
    "景点": ("景点", "自然景点", "海岛景点", "自然景观", "海岛"),
    "交通": ("交通", "怎么走", "换乘", "公交", "地铁"),
    "美食": ("美食", "小吃", "吃什么", "特色菜"),
    "季节": ("雨季", "夏季", "季节", "雨天", "夏天"),
    "避坑": ("避坑", "高原", "鼓浪屿", "误区", "踩坑", "注意什么"),
}
_UNSUPPORTED_REQUEST_MARKERS = (
    "实时预订",
    "绝对不会",
    "不存在的火星",
    "没有来源",
    "此刻排队",
    "今天是否临时闭馆",
    "替我支付",
    "精确降雨量",
    "实时空位",
    "断言某景点",
    "实时最低机票",
    "绝对安全",
    "最新票价",
    "今天还有票",
    "最便宜的酒店",
)


class _OfflineRagEmbedder:
    """Turn a supported pilot-region-and-topic question into a deterministic vector."""

    def embed(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for text in texts:
            code = 0
            topic_code = 0
            if not any(marker in text for marker in _UNSUPPORTED_REQUEST_MARKERS):
                if "厦门" in text:
                    code = 3
                elif "福建" in text:
                    code = 1
                elif "云南" in text:
                    code = 2
                for index, (topic, markers) in enumerate(_RAG_TOPIC_MARKERS.items(), start=1):
                    if any(marker in text for marker in markers):
                        topic_code = index
                        break
            vector = [0.0] * EMBEDDING_DIMENSIONS
            vector[0] = float(code)
            vector[1] = float(topic_code)
            vectors.append(vector)
        return vectors


class _OfflineRagRepository:
    def search(
        self,
        query_vector: list[float],
        region: str | None,
        limit: int,
    ) -> list[RetrievedChunk]:
        del region
        code = int(query_vector[0]) if query_vector else 0
        topic_code = int(query_vector[1]) if len(query_vector) > 1 else 0
        region_info = _RAG_REGIONS.get(code)
        if region_info is None or not 1 <= topic_code <= len(_RAG_TOPICS):
            return []
        region_name, source_label = region_info
        topic, evidence_by_region = tuple(_RAG_TOPICS.items())[topic_code - 1]
        evidence = evidence_by_region[code - 1]
        content = f"{region_name}{evidence}的试点资料已核对，可据此提供旅行建议。"
        return [
            RetrievedChunk(
                chunk_id=f"offline-{code}-{topic}",
                content=content,
                source_label=source_label,
                score=0.95,
            )
        ][:limit]


_RAG_WEATHER_REPORT_DAY = date(2026, 8, 13)
_RAG_WEATHER_REPORT_TIME = datetime(
    2026, 8, 13, 8, 0, tzinfo=timezone(timedelta(hours=8))
)


class _OfflineWeatherProvider:
    def weather(self, adcode: str, extensions: str) -> ProviderResult[AmapWeatherPayload]:
        if extensions != "all":
            raise AssertionError("RAG/weather boundary evaluation requires forecast mode")
        city = {"350200": "厦门市", "350000": "福建省", "530000": "云南省"}[adcode]
        casts = tuple(
            AmapForecastCast(
                date=_RAG_WEATHER_REPORT_DAY + timedelta(days=offset),
                day_weather="晴",
                night_weather="多云",
                day_temperature="30",
                night_temperature="22",
            )
            for offset in range(3)
        )
        return ProviderResult(
            data=AmapWeatherPayload(
                forecast=AmapForecast(
                    province=city,
                    city=city,
                    adcode=adcode,
                    report_time=_RAG_WEATHER_REPORT_TIME,
                    casts=casts,
                )
            ),
            source=AMAP_WEATHER_SOURCE,
            fetched_at=_RAG_WEATHER_REPORT_TIME,
        )


def run_rag_weather_case(case: RagWeatherCase) -> RagWeatherPrediction:
    if case.category == "weather_boundary":
        if case.city_id is None or case.day_offset is None:
            raise ValueError(f"weather case {case.id} is missing city_id or day_offset")
        weather = WeatherService(
            provider=_OfflineWeatherProvider(),
            cache_ttl_seconds=60,
            daily_limit=1,
            today=lambda: _RAG_WEATHER_REPORT_DAY,
        ).daily_weather(
            case.city_id,
            _RAG_WEATHER_REPORT_DAY + timedelta(days=case.day_offset),
        )
        return RagWeatherPrediction(
            weather_status="available" if weather is not None else "unavailable"
        )

    # Some paraphrased evaluation prompts omit the place name. The production
    # request still carries the selected region, so include that routing context
    # when exercising the deterministic offline embedder.
    query = f"{case.region or ''}{case.question}"
    answer = KnowledgeAnswerService(
        _OfflineRagRepository(),
        _OfflineRagEmbedder(),
        threshold=0.7,
    ).answer(query, region=case.region)
    source_labels = tuple(chunk.source_label for chunk in answer.chunks)
    topics = tuple(
        topic
        for topic, markers in _RAG_TOPIC_MARKERS.items()
        if any(marker in chunk.content for chunk in answer.chunks for marker in markers)
    )
    evidence = tuple(chunk.content for chunk in answer.chunks)
    citation_complete = bool(source_labels) and all(
        answer.reply.count(f"【来源：{label}】") == 1
        for label in source_labels
    )
    return RagWeatherPrediction(
        status=answer.status,
        source_labels=source_labels,
        topics=topics,
        evidence=evidence,
        citation_complete=citation_complete,
    )


def score_rag_weather(
    predictions: list[RagWeatherPrediction],
    cases: list[RagWeatherCase],
) -> RagWeatherEvaluationReport:
    if len(predictions) != len(cases):
        raise ValueError("RAG/weather predictions and cases must have the same length")
    grounded = [(prediction, case) for prediction, case in zip(predictions, cases) if case.category == "grounded"]
    refusals = [(prediction, case) for prediction, case in zip(predictions, cases) if case.category == "refusal"]
    citation_required = [
        (prediction, case)
        for prediction, case in zip(predictions, cases)
        if case.category in {"grounded", "citation_safety"}
    ]
    weather_cases = [(prediction, case) for prediction, case in zip(predictions, cases) if case.category == "weather_boundary"]
    grounded_source_rate = _ratio(
        sum(
            prediction.status == "grounded"
            and bool(prediction.source_labels)
            and set(prediction.source_labels).issubset(case.allowed_sources)
            and case.expected_topic in prediction.topics
            and any(case.expected_evidence in item for item in prediction.evidence)
            for prediction, case in grounded
        ),
        len(grounded),
    )
    refusal_accuracy = _ratio(
        sum(prediction.status == "refused" for prediction, _ in refusals),
        len(refusals),
    )
    citation_completeness = _ratio(
        sum(
            prediction.status == "grounded"
            and prediction.citation_complete
            and set(prediction.source_labels) == set(case.allowed_sources)
            and case.expected_topic in prediction.topics
            and any(case.expected_evidence in item for item in prediction.evidence)
            for prediction, case in citation_required
        ),
        len(citation_required),
    )
    weather_boundary_accuracy = _ratio(
        sum(
            prediction.weather_status == case.expected_weather_status
            for prediction, case in weather_cases
        ),
        len(weather_cases),
    )
    failures: dict[str, list[str]] = {}
    for prediction, case in zip(predictions, cases):
        reasons: list[str] = []
        if case.category == "grounded" and not (
            prediction.status == "grounded"
            and bool(prediction.source_labels)
            and set(prediction.source_labels).issubset(case.allowed_sources)
            and case.expected_topic in prediction.topics
            and any(case.expected_evidence in item for item in prediction.evidence)
        ):
            reasons.append("grounded_source: missing, unexpected, or irrelevant evidence")
        if case.category == "refusal" and prediction.status != "refused":
            reasons.append("refusal: unsafe grounded answer")
        if case.category in {"grounded", "citation_safety"} and not (
            prediction.status == "grounded"
            and prediction.citation_complete
            and set(prediction.source_labels) == set(case.allowed_sources)
            and case.expected_topic in prediction.topics
            and any(case.expected_evidence in item for item in prediction.evidence)
        ):
            reasons.append("citation: incomplete or unexpected source")
        if case.category == "weather_boundary" and prediction.weather_status != case.expected_weather_status:
            reasons.append(
                f"weather_boundary: expected {case.expected_weather_status}, got {prediction.weather_status}"
            )
        if reasons:
            failures[case.id] = reasons
    return RagWeatherEvaluationReport(
        total_cases=len(cases),
        grounded_source_rate=grounded_source_rate,
        refusal_accuracy=refusal_accuracy,
        citation_completeness=citation_completeness,
        weather_boundary_accuracy=weather_boundary_accuracy,
        denominators={
            "grounded": len(grounded),
            "refusal": len(refusals),
            "citation_required": len(citation_required),
            "weather_boundary": len(weather_cases),
        },
        failures=failures,
    )


def evaluate_rag_weather(cases: list[RagWeatherCase]) -> RagWeatherEvaluationReport:
    return score_rag_weather([run_rag_weather_case(case) for case in cases], cases)


def rag_weather_gate_passes(report: RagWeatherEvaluationReport) -> bool:
    payload = report.to_dict()
    return all(
        payload[name] >= threshold
        for name, threshold in REQUIRED_RAG_WEATHER_METRICS.items()
    )


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    position = (len(ordered) - 1) * percentile
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def run_production_composition_evaluation() -> ProductionCompositionReport:
    """Exercise production orchestration with explicit offline external seams.

    This intentionally uses the deployed rule classifier/extractor, application
    confirmation protocol and trip persistence service. Only paid/network
    boundaries are replaced, so the result must not be represented as a live
    provider or model benchmark.
    """
    repository = InMemoryTripRepository()
    trip_service = TripService(repository)
    planner = FixtureStructuredPlanner(None)
    evidence_provider = FixtureEvidenceProvider(["production-flow-source"])
    usage_repository = InMemoryUsageRepository()
    usage_guard = UsageGuard(
        repository=usage_repository,
        user_daily_limit=10,
        global_daily_limit=10,
        enabled=True,
        provider_configured=True,
        clock=lambda: FIXTURE_NOW,
    )

    def agent_factory(initial_profile: TravelProfile) -> SafeTravelAgent:
        return SafeTravelAgent(
            classifier=RuleIntentClassifier(),
            extractor=RuleTravelExtractor(),
            planner=planner,
            evidence_provider=evidence_provider,
            initial_profile=initial_profile,
        )

    application = TravelChatApplication(
        agent_factory=agent_factory,
        usage_guard=usage_guard,
        confirmation_store=ConfirmationStore(),
        trip_service=trip_service,
    )
    user_id = UUID("11111111-1111-4111-8111-111111111111")
    subject = "user:production-evaluation"
    thread_id = "production-flow"
    state: dict[str, Any] = {}
    steps: list[ProductionFlowStep] = []

    def run_step(name: str, operation: Any) -> None:
        started = perf_counter()
        failure_type = None
        try:
            operation()
            success = True
        except Exception as exc:
            success = False
            failure_type = type(exc).__name__
        steps.append(
            ProductionFlowStep(
                name=name,
                success=success,
                latency_ms=round((perf_counter() - started) * 1000, 3),
                failure_type=failure_type,
            )
        )

    def plan_and_save() -> None:
        collected = application.collect(
            user_id=user_id,
            subject=subject,
            thread_id=thread_id,
            trip_id=None,
            message="从上海到杭州 2026-10-01 至 2026-10-02 2人 预算3000元",
        )
        if collected.stage != "confirming":
            raise RuntimeError("plan did not reach confirmation")
        planned = application.confirm(
            user_id=user_id,
            subject=subject,
            quota_subject=subject,
            thread_id=thread_id,
            trip_id=None,
            message="确认",
        )
        if planned.stage != "planned" or planned.trip_id is None:
            raise RuntimeError("plan was not atomically saved")
        state["trip_id"] = planned.trip_id

    def smalltalk() -> None:
        result = application.collect(
            user_id=user_id,
            subject=subject,
            thread_id=thread_id,
            trip_id=None,
            message="hello",
        )
        if result.stage != "collecting" or result.intent != "smalltalk":
            raise RuntimeError("smalltalk production route was not reached")

    def unsupported() -> None:
        result = application.collect(
            user_id=user_id,
            subject=subject,
            thread_id=thread_id,
            trip_id=None,
            message="write my Java homework",
        )
        if result.error_code != "OUT_OF_SCOPE" or result.intent != "unsupported":
            raise RuntimeError("unsupported production route was not refused")

    def modify_and_save() -> None:
        trip_id = state["trip_id"]
        previous = trip_service.get_trip(user_id, trip_id)
        if previous.itinerary is None:
            raise RuntimeError("saved itinerary is missing before modification")
        instruction = "the second day不要太赶，预算3200元"
        collected = application.collect(
            user_id=user_id,
            subject=subject,
            thread_id=thread_id,
            trip_id=trip_id,
            message=instruction,
        )
        if collected.stage != "confirming" or collected.intent != "modify_trip":
            raise RuntimeError("modify did not reach confirmation")
        modified = application.confirm(
            user_id=user_id,
            subject=subject,
            quota_subject=subject,
            thread_id=thread_id,
            trip_id=trip_id,
            message="确认",
        )
        if (
            modified.stage != "planned"
            or modified.itinerary is None
            or modified.itinerary.budget.trip_total != 3200
            or not planner.revisions
            or planner.revisions[-1][0] != previous.itinerary
            or planner.revisions[-1][1] != instruction
            or modified.itinerary.title
            != f"fixture-revision-{planner.revisions[-1][2]}"
        ):
            raise RuntimeError("revision did not consume and persist the saved plan/instruction")

    def explain() -> None:
        explained = application.collect(
            user_id=user_id,
            subject=subject,
            thread_id=thread_id,
            trip_id=state["trip_id"],
            message="为什么这样安排第一天？",
        )
        if explained.stage != "planned" or explained.intent != "explain_trip":
            raise RuntimeError("saved itinerary was not explained")

    def reopen() -> None:
        reopened = trip_service.get_trip(user_id, state["trip_id"])
        if reopened.status != "planned" or reopened.profile.budget_cny != 3200:
            raise RuntimeError("modified itinerary could not be reopened")

    for name, operation in (
        ("smalltalk", smalltalk),
        ("unsupported", unsupported),
        ("plan_and_save", plan_and_save),
        ("modify_and_save", modify_and_save),
        ("explain", explain),
        ("reopen", reopen),
    ):
        run_step(name, operation)

    latencies = [step.latency_ms for step in steps]
    successes = sum(step.success for step in steps)
    usage = usage_repository.get_daily(subject, FIXTURE_NOW.date())
    return ProductionCompositionReport(
        mode="production_composition_offline_seams",
        harness_version=PRODUCTION_FLOW_HARNESS_VERSION,
        production_components=(
            "RuleIntentClassifier",
            "RuleTravelExtractor",
            "SafeTravelAgent",
            "TravelChatApplication",
            "TripService",
        ),
        seam_disclosure=(
            "InMemoryTripRepository plus deterministic planner/provider fixtures replace "
            "Supabase, network providers and the paid model."
        ),
        change_summary=(
            "Added a production-rule plan/confirm/persist/modify/explain/reopen flow.",
            "Separated the 80-case component fixture gate from production composition evidence.",
            "Added deterministic latency and explicit zero-cost offline accounting metadata.",
        ),
        steps=tuple(steps),
        success_rate=_ratio(successes, len(steps)),
        p50_latency_ms=round(_percentile(latencies, 0.50), 3),
        p95_latency_ms=round(_percentile(latencies, 0.95), 3),
        model_calls=usage.model_calls,
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        estimated_cost_micros=usage.estimated_cost_micros,
        cost_basis=(
            "offline external seams: no paid-model or network call; production billing "
            "must use recorded model calls/tokens and configured rates"
        ),
    )


def _write_report(
    report: EvaluationReport,
    output: Path,
    thresholds: dict[str, float],
    known_failures: list[str],
    rag_weather_report: RagWeatherEvaluationReport | None = None,
) -> bool:
    output.mkdir(parents=True, exist_ok=True)
    payload = report.to_dict()
    production_report = run_production_composition_evaluation()
    payload["evaluation_mode"] = "offline_component_fixtures"
    payload["harness_version"] = OFFLINE_COMPONENT_HARNESS_VERSION
    payload["change_summary"] = [
        "The fixed 80-case corpus remains the versioned component regression gate.",
        "Production rule/application composition is reported separately with offline external seams.",
    ]
    payload["production_composition"] = production_report.to_dict()
    if rag_weather_report is not None:
        payload["rag_weather"] = rag_weather_report.to_dict()
        payload["rag_weather"]["harness_version"] = RAG_WEATHER_HARNESS_VERSION
        payload["rag_weather"]["thresholds"] = REQUIRED_RAG_WEATHER_METRICS
    failed_thresholds = []
    for metric, threshold in thresholds.items():
        value = payload[metric]
        failed = value > threshold if metric == "unsupported_fact_rate" else value < threshold
        if failed:
            failed_thresholds.append(metric)
    if production_report.success_rate < 1.0:
        failed_thresholds.append("production_composition_success")
    if rag_weather_report is not None:
        rag_payload = rag_weather_report.to_dict()
        failed_thresholds.extend(
            f"rag_weather.{name}"
            for name, threshold in REQUIRED_RAG_WEATHER_METRICS.items()
            if rag_payload[name] < threshold
        )
    payload["thresholds"] = thresholds
    payload["failed_thresholds"] = failed_thresholds
    payload["known_failures"] = known_failures
    (output / "evaluation-report.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    rows = "\n".join(f"| {metric} | {payload[metric]:.2%} | {threshold:.2%} | {'FAIL' if metric in failed_thresholds else 'PASS'} |" for metric, threshold in thresholds.items())
    production_status = "PASS" if production_report.success_rate == 1.0 else "FAIL"
    rag_weather_markdown = ""
    if rag_weather_report is not None:
        rag_rows = "\n".join(
            f"| {name} | {getattr(rag_weather_report, name):.2%} | {threshold:.2%} | "
            f"{'PASS' if getattr(rag_weather_report, name) >= threshold else 'FAIL'} |"
            for name, threshold in REQUIRED_RAG_WEATHER_METRICS.items()
        )
        rag_weather_markdown = (
            f"\n\n## RAG/weather pilot (offline seams)\n\n"
            f"Cases: {rag_weather_report.total_cases}; harness: `{RAG_WEATHER_HARNESS_VERSION}`. "
            "No network or paid-model calls are made.\n\n"
            "| Metric | Result | Gate | Status |\n|---|---:|---:|---|\n"
            f"{rag_rows}\n\nFailures: "
            f"`{json.dumps(rag_weather_report.failures, ensure_ascii=False, sort_keys=True)}`"
        )
    markdown = f"# Offline evaluation report\n\nCases: {report.total_cases}. Evaluation mode: `offline_component_fixtures`; harness: `{OFFLINE_COMPONENT_HARNESS_VERSION}`. The existing 80-case gate uses fixed model/provider fixtures and does not make network calls.\n\n| Metric | Result | Gate | Status |\n|---|---:|---:|---|\n{rows}\n\nFailures: `{json.dumps(report.failures, ensure_ascii=False, sort_keys=True)}`\n\nMetric denominators: `{json.dumps(report.denominators, ensure_ascii=False, sort_keys=True)}`.\n\n## Production composition (offline seams)\n\nHarness: `{production_report.harness_version}`. Success: {production_report.success_rate:.2%} (gate: 100%, {production_status}); P50: {production_report.p50_latency_ms:.3f} ms; P95: {production_report.p95_latency_ms:.3f} ms. Model calls/input/output/cost: {production_report.model_calls}/{production_report.input_tokens}/{production_report.output_tokens}/{production_report.estimated_cost_micros} micro-CNY. This is not a paid-model or network benchmark. {production_report.seam_disclosure}{rag_weather_markdown}\n"
    (output / "evaluation-report.md").write_text(markdown, encoding="utf-8")
    return not failed_thresholds


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the versioned offline travel-agent evaluation.")
    parser.add_argument("--cases", default="tests/evaluation/cases.jsonl")
    parser.add_argument("--rag-weather-cases", default=str(RAG_WEATHER_CASES_PATH))
    parser.add_argument("--output", default="build/evaluation")
    parser.add_argument("--live", action="store_true", help="Reserved; a paid live run requires ALLOW_PAID_EVAL=true.")
    args = parser.parse_args()
    if args.live and (os.getenv("ALLOW_PAID_EVAL") != "true" or not os.getenv("DEEPSEEK_API_KEY")):
        raise SystemExit("--live requires ALLOW_PAID_EVAL=true and DEEPSEEK_API_KEY; no network call was made.")
    if args.live:
        raise SystemExit("Live harness is intentionally not bundled with the offline gate.")
    baseline = load_baseline()
    report = evaluate(load_cases(args.cases))
    rag_weather_report = evaluate_rag_weather(
        load_rag_weather_cases(args.rag_weather_cases)
    )
    passed = _write_report(
        report,
        Path(args.output),
        baseline["thresholds"],
        baseline["known_failures"],
        rag_weather_report,
    )
    print(
        json.dumps(
            {"rag_weather_cases": rag_weather_report.total_cases, **{
                name: getattr(rag_weather_report, name)
                for name in REQUIRED_RAG_WEATHER_METRICS
            }},
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
