from pathlib import Path

from tests.evaluation.runner import EvaluationCase, Prediction, load_cases, score


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
