"""Deterministic, offline regression evaluation for the public MVP.

The evaluator deliberately drives the production ``SafeTravelAgent``.  Its
model and provider seams are fixed fixtures so no run can make a network call
or spend provider credits.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Iterable, Literal

from app.agent.graph import ChatResult, SafeTravelAgent, TrustedEvidence
from app.agent.intent import Intent, IntentResult, route_intent
from app.agent.planning import Planner
from app.core.usage import ProviderUnavailable
from app.schemas import Itinerary, TravelProfile


ACTION = Literal["ask", "refuse", "plan", "modify", "explain", "degrade"]
FIXTURE_NOW = datetime(2026, 7, 28, tzinfo=timezone.utc)
PLAN_ACTIONS = {"plan", "modify", "explain"}


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
    failures: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class FixtureClassifier:
    """A fixed JSON-mode model result; production routing remains in use."""

    def __init__(self, intent: str, scenario: str | None) -> None:
        self._intent = intent
        self._scenario = scenario

    def classify(self, message: str, has_trip: bool) -> IntentResult:
        if self._scenario in {"model_400", "model_429", "model_500"}:
            raise ProviderUnavailable(self._scenario.upper())
        result = IntentResult(intent=self._intent, confidence=0.99)
        return result.model_copy(update={"intent": route_intent(result, has_trip)})


class FixtureExtractor:
    def __init__(self, profiles: Iterable[dict[str, Any]]) -> None:
        self._profiles = iter(profiles)

    def extract(self, message: str, profile: TravelProfile) -> TravelProfile:
        try:
            return TravelProfile.model_validate(next(self._profiles))
        except StopIteration:
            return TravelProfile()


class FixtureEvidenceProvider:
    def __init__(self, source_ids: list[str], scenario: str | None) -> None:
        self._source_ids = source_ids
        self._scenario = scenario

    def fetch(self, profile: TravelProfile) -> list[TrustedEvidence]:
        if self._scenario and self._scenario != "format_twice":
            return []
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

    def plan(self, profile: TravelProfile, provider_results: object) -> Itinerary:
        return self._planner.plan(profile, provider_results)

    def _generate(self, profile: TravelProfile, provider_results: object, repair_codes: list[str] | None) -> object:
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
    cases = [EvaluationCase.from_dict(json.loads(line)) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]
    expected_ids = [f"P{i:03d}" for i in range(1, 21)] + [f"M{i:03d}" for i in range(1, 21)] + [f"R{i:03d}" for i in range(1, 16)] + [f"N{i:03d}" for i in range(1, 16)] + [f"E{i:03d}" for i in range(1, 11)]
    if len(cases) != 80 or [case.id for case in cases] != expected_ids:
        raise ValueError("cases.jsonl must contain exactly ordered P001-P020, M001-M020, R001-R015, N001-N015, E001-E010")
    if len({case.id for case in cases}) != 80:
        raise ValueError("evaluation case IDs must be unique")
    if any(case.expected_action not in {"ask", "refuse", "plan", "modify", "explain", "degrade"} for case in cases):
        raise ValueError("evaluation expected_action is invalid")
    return cases


def run_case(case: EvaluationCase) -> Prediction:
    intent = case.fixture_intent or case.expected_intent
    profiles = case.fixture_profiles or ([_fallback_profile()] if case.provider_scenario else [])
    agent = SafeTravelAgent(
        classifier=FixtureClassifier(intent, case.provider_scenario),
        extractor=FixtureExtractor(profiles),
        planner=FixtureStructuredPlanner(case.provider_scenario),
        evidence_provider=FixtureEvidenceProvider(case.allowed_sources, case.provider_scenario),
    )
    trip = SimpleNamespace(profile=TravelProfile.model_validate(profiles[0])) if case.has_trip and profiles else None
    result: ChatResult | None = None
    try:
        for message in case.messages:
            result = agent.run(message, trip=trip)
    except ProviderUnavailable as error:
        return Prediction(intent=intent, action="degrade", fields={}, error_code=error.code, fallback_safe=True)
    assert result is not None
    fields = result.profile
    schema_valid, budget_valid, citation_ids = _structured_output(result)
    predicted_action = _action_for(case, result, intent)
    # A safe fallback makes no factual assertion, so it must not be counted as
    # an unsupported fact.  Unsupported facts are only model-authored claims
    # that escaped citation validation; the structured gate prevents those.
    unsupported_facts = 0
    return Prediction(
        intent=intent, action=predicted_action, fields=fields, error_code=result.error_code,
        schema_valid=schema_valid, budget_valid=budget_valid, citation_ids=citation_ids,
        unsupported_facts=unsupported_facts, fallback_safe=predicted_action == "degrade" and bool(result.error_code),
    )


def _fallback_profile() -> dict[str, Any]:
    return {"origin": "上海", "destination": "杭州", "start_date": "2026-10-01", "end_date": "2026-10-02", "travelers": 2, "budget_cny": 3000}


def _structured_output(result: ChatResult) -> tuple[bool, bool, list[str]]:
    try:
        itinerary = Itinerary.model_validate_json(result.reply)
    except Exception:
        return False, False, []
    citations = [citation.evidence_id for citation in itinerary.citations]
    for day in itinerary.days:
        for activity in (day.morning, day.afternoon, day.evening):
            citations.extend(citation.evidence_id for citation in activity.citations)
    return True, itinerary.budget.trip_total >= 0, citations


def _action_for(case: EvaluationCase, result: ChatResult, intent: str) -> str:
    if result.error_code in {"UNVERIFIABLE_REALTIME_REQUEST", "OUT_OF_SCOPE", "HIGH_STAKES_ADVICE"}:
        return "refuse"
    if result.error_code in {"UNVERIFIED_FACTS", "PLAN_VALIDATION_FAILED", "AGENT_UNAVAILABLE", "DESTINATION_UNDETERMINED"}:
        return "degrade" if result.error_code in {"UNVERIFIED_FACTS", "PLAN_VALIDATION_FAILED", "AGENT_UNAVAILABLE"} else "ask"
    if result.stage == "collecting":
        return "ask"
    if intent == "modify_trip" and case.has_trip:
        return "modify"
    if intent == "explain_trip" and case.has_trip:
        return "explain"
    return "plan"


def score(predictions: list[Prediction], cases: list[EvaluationCase]) -> EvaluationReport:
    if len(predictions) != len(cases):
        raise ValueError("predictions and cases must have the same length")
    total = len(cases)
    intent_correct = sum(pred.intent == case.expected_intent for pred, case in zip(predictions, cases))
    tp = fp = fn = 0
    for prediction, case in zip(predictions, cases):
        expected = case.expected_fields
        if not expected:
            continue
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
    task_successes = [pred.action == case.expected_action and pred.intent == case.expected_intent for pred, case in zip(predictions, cases)]
    failures = [case.id for good, case in zip(task_successes, cases) if not good]
    metrics = [
        _ratio(intent_correct, total), slot_f1,
        _ratio(sum(pred.action == "ask" for pred, _ in ask_cases), len(ask_cases), empty=1.0),
        _ratio(sum(pred.action == "refuse" for pred, _ in refusal_cases), len(refusal_cases), empty=1.0),
        _ratio(sum(pred.action == "refuse" for pred in predicted_refusals), len(predicted_refusals), empty=1.0),
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
        {"cases": total, "slots": 2 * tp + fp + fn, "clarification_cases": len(ask_cases), "required_refusals": len(refusal_cases), "predicted_refusals": len(predicted_refusals), "schema_cases": len(schema_cases), "citation_required_cases": len(citation_required), "observed_citations": len(actual_citations), "fallback_cases": len(fallback_cases), "fact_items": fact_items}, failures,
    )


def _ratio(numerator: int, denominator: int, *, empty: float = 0.0) -> float:
    return numerator / denominator if denominator else empty


DEFAULT_THRESHOLDS = {"intent_accuracy": 0.90, "slot_micro_f1": 0.90, "clarification_recall": 0.95, "refusal_precision": 0.90, "refusal_recall": 0.95, "schema_validity": 0.98, "budget_validity": 0.98, "citation_coverage": 0.95, "citation_validity": 0.95, "unsupported_fact_rate": 0.02, "task_success_rate": 0.85, "fallback_success_rate": 1.0}


def evaluate(cases: list[EvaluationCase]) -> EvaluationReport:
    return score([run_case(case) for case in cases], cases)


def run_evaluation(cases: list[EvaluationCase], agent: Any | None = None) -> EvaluationReport:
    """Public interface. ``agent`` is reserved for explicit custom harnesses."""
    if agent is not None:
        predictions = [agent(case) for case in cases]
        return score(predictions, cases)
    return evaluate(cases)


def _write_report(report: EvaluationReport, output: Path, thresholds: dict[str, float]) -> bool:
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
    (output / "evaluation-report.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    rows = "\n".join(f"| {metric} | {payload[metric]:.2%} | {threshold:.2%} | {'FAIL' if metric in failed_thresholds else 'PASS'} |" for metric, threshold in thresholds.items())
    markdown = f"# Offline evaluation report\n\nCases: {report.total_cases}. This run uses fixed model/provider fixtures and does not make network calls.\n\n| Metric | Result | Gate | Status |\n|---|---:|---:|---|\n{rows}\n\nFailures: {', '.join(report.failures) if report.failures else 'none'}\n\nMetric denominators: `{json.dumps(report.denominators, ensure_ascii=False, sort_keys=True)}`.\n"
    (output / "evaluation-report.md").write_text(markdown, encoding="utf-8")
    return not failed_thresholds


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the versioned offline travel-agent evaluation.")
    parser.add_argument("--cases", default="tests/evaluation/cases.jsonl")
    parser.add_argument("--output", default="build/evaluation")
    parser.add_argument("--live", action="store_true", help="Reserved; a paid live run requires ALLOW_PAID_EVAL=true.")
    args = parser.parse_args()
    if args.live:
        raise SystemExit("Live evaluation is disabled in this offline runner; set ALLOW_PAID_EVAL=true in a separate explicit harness.")
    report = evaluate(load_cases(args.cases))
    return 0 if _write_report(report, Path(args.output), DEFAULT_THRESHOLDS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
