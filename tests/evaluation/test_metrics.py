from pathlib import Path

from tests.evaluation.runner import EvaluationCase, Prediction, load_baseline, load_cases, run_case, score


def test_metric_formulas_count_intents_slots_and_success() -> None:
    case = EvaluationCase(
        id="unit-001",
        category="unit",
        messages=["plan"],
        expected_intent="plan_trip",
        expected_fields={"origin": "上海"},
        expected_action="plan",
        allowed_sources=[],
    )
    report = score(
        [Prediction(intent="plan_trip", action="plan", fields={"origin": "上海"})],
        [case],
    )

    assert report.intent_accuracy == 1.0
    assert report.slot_micro_f1 == 1.0
    assert report.task_success_rate == 1.0


def test_metric_formulas_keep_refusal_denominators_explicit() -> None:
    cases = [
        EvaluationCase("unit-ask", "unit", ["x"], "plan_trip", {}, "ask", []),
        EvaluationCase("unit-refuse", "unit", ["x"], "unsupported", {}, "refuse", []),
    ]
    report = score(
        [
            Prediction(intent="plan_trip", action="ask", fields={}),
            Prediction(intent="unsupported", action="plan", fields={}),
        ],
        cases,
    )

    assert report.clarification_recall == 1.0
    # No refusal was predicted, so precision is vacuously one; the denominator
    # remains visible in the report and recall records the missed refusal.
    assert report.refusal_precision == 1.0
    assert report.refusal_recall == 0.0
    assert report.task_success_rate == 0.5


def test_versioned_corpus_has_the_required_fixed_strata() -> None:
    cases = load_cases(Path(__file__).with_name("cases.jsonl"))

    assert len(cases) == 80
    assert [case.category for case in cases].count("complete_domestic") == 20
    assert [case.category for case in cases].count("missing_or_conflict") == 20
    assert [case.category for case in cases].count("refusal") == 15
    assert [case.category for case in cases].count("natural_language") == 15
    assert [case.category for case in cases].count("exception") == 10


def test_refusal_precision_penalizes_a_false_positive() -> None:
    cases = [
        EvaluationCase("refuse", "unit", ["x"], "unsupported", {}, "refuse", []),
        EvaluationCase("plan", "unit", ["x"], "plan_trip", {}, "plan", []),
    ]
    report = score([
        Prediction("unsupported", "refuse", {}),
        Prediction("plan_trip", "refuse", {}),
    ], cases)

    assert report.refusal_recall == 1.0
    assert report.refusal_precision == 0.5
    assert report.denominators["refusal_true_positives"] == 1


def test_expected_error_is_part_of_task_success_and_failure_reason() -> None:
    case = EvaluationCase("error", "unit", ["x"], "plan_trip", {}, "degrade", [], expected_error="WEATHER_TIMEOUT")
    report = score([Prediction("plan_trip", "degrade", {}, error_code="PLACES_TIMEOUT", fallback_safe=True)], [case])

    assert report.task_success_rate == 0.0
    assert report.failures == {"error": ["error_code: expected WEATHER_TIMEOUT, got PLACES_TIMEOUT"]}


def test_unsupported_claim_without_a_citation_is_counted() -> None:
    case = EvaluationCase("claim", "unit", ["x"], "plan_trip", {}, "plan", [])
    report = score([Prediction("plan_trip", "plan", {}, unsupported_facts=1)], [case])

    assert report.unsupported_fact_rate == 1.0
    assert report.denominators["fact_items"] == 1


def test_fixture_predictions_do_not_change_when_expectations_change() -> None:
    original = next(case for case in load_cases(Path(__file__).with_name("cases.jsonl")) if case.id == "P001")
    changed = EvaluationCase(
        original.id, original.category, original.messages, "unsupported", {"origin": "不存在"}, "refuse", original.allowed_sources,
        expected_error="OUT_OF_SCOPE",
    )

    prediction = run_case(original)
    assert run_case(changed) == prediction


def test_baseline_is_the_only_gate_configuration() -> None:
    baseline = load_baseline(Path(__file__).with_name("baseline.json"))
    assert baseline["thresholds"]["schema_validity"] == 0.98
    assert baseline["known_failures"] == ["P015", "P019"]
