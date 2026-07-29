"""Deterministic, offline regression evaluation for the public MVP.

Ordinary journeys drive ``SafeTravelAgent`` while exception cases exercise the
named production component directly. Fixed seams prevent network or paid-model
calls.
"""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Literal
from uuid import uuid4

import httpx

from app.agent.graph import ChatResult, SafeTravelAgent, TrustedEvidence, extract_profile
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
from tests.evaluation.offline_fixtures import OfflineModel, SCENARIO_BY_MESSAGE, model_factory, source_ids_for


ACTION = Literal["ask", "refuse", "plan", "modify", "explain", "degrade"]
FIXTURE_NOW = datetime(2026, 7, 28, tzinfo=timezone.utc)


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

    def extract(self, message: str, profile: TravelProfile) -> TravelProfile:
        return extract_profile(message, profile, model_factory=model_factory)


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

    def plan(self, profile: TravelProfile, provider_results: object) -> Itinerary:
        return self._planner.plan(profile, provider_results)

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
    evidence_provider = FixtureEvidenceProvider([])
    agent = SafeTravelAgent(
        classifier=classifier,
        extractor=OfflineExtractor(),
        planner=FixtureStructuredPlanner(scenario),
        evidence_provider=evidence_provider,
    )
    trip = None
    result: ChatResult | None = None
    try:
        for index, message in enumerate(case.messages):
            evidence_provider.use_message(message)
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
    fields = result.profile
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
        itinerary = Itinerary.model_validate_json(result.reply)
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
        itinerary = Itinerary.model_validate_json(result.reply)
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


def _write_report(report: EvaluationReport, output: Path, thresholds: dict[str, float], known_failures: list[str]) -> bool:
    output.mkdir(parents=True, exist_ok=True)
    payload = report.to_dict()
    failed_thresholds = []
    for metric, threshold in thresholds.items():
        value = payload[metric]
        failed = value > threshold if metric == "unsupported_fact_rate" else value < threshold
        if failed:
            failed_thresholds.append(metric)
    payload["thresholds"] = thresholds
    payload["failed_thresholds"] = failed_thresholds
    payload["known_failures"] = known_failures
    (output / "evaluation-report.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    rows = "\n".join(f"| {metric} | {payload[metric]:.2%} | {threshold:.2%} | {'FAIL' if metric in failed_thresholds else 'PASS'} |" for metric, threshold in thresholds.items())
    markdown = f"# Offline evaluation report\n\nCases: {report.total_cases}. This run uses fixed model/provider fixtures and does not make network calls.\n\n| Metric | Result | Gate | Status |\n|---|---:|---:|---|\n{rows}\n\nFailures: `{json.dumps(report.failures, ensure_ascii=False, sort_keys=True)}`\n\nMetric denominators: `{json.dumps(report.denominators, ensure_ascii=False, sort_keys=True)}`.\n"
    (output / "evaluation-report.md").write_text(markdown, encoding="utf-8")
    return not failed_thresholds


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the versioned offline travel-agent evaluation.")
    parser.add_argument("--cases", default="tests/evaluation/cases.jsonl")
    parser.add_argument("--output", default="build/evaluation")
    parser.add_argument("--live", action="store_true", help="Reserved; a paid live run requires ALLOW_PAID_EVAL=true.")
    args = parser.parse_args()
    if args.live and (os.getenv("ALLOW_PAID_EVAL") != "true" or not os.getenv("DEEPSEEK_API_KEY")):
        raise SystemExit("--live requires ALLOW_PAID_EVAL=true and DEEPSEEK_API_KEY; no network call was made.")
    if args.live:
        raise SystemExit("Live harness is intentionally not bundled with the offline gate.")
    baseline = load_baseline()
    report = evaluate(load_cases(args.cases))
    return 0 if _write_report(report, Path(args.output), baseline["thresholds"], baseline["known_failures"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
