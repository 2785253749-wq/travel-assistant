from dataclasses import replace
from pathlib import Path

import pytest

from app.schemas import TravelProfile
from tests.evaluation import runner
from tests.evaluation.runner import EvaluationCase, Prediction, load_baseline, load_cases, run_case, score
from tests.evaluation.offline_fixtures import OfflineModel, SCENARIO_BY_MESSAGE, model_factory


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


def test_natural_language_cases_have_independent_extractable_slot_oracles() -> None:
    cases = [
        case
        for case in load_cases(Path(__file__).with_name("cases.jsonl"))
        if case.category == "natural_language"
    ]

    assert sum(bool(case.expected_fields) for case in cases) >= 12
    for case in cases:
        if not case.expected_fields:
            continue
        profile = TravelProfile()
        for message in case.messages:
            profile = runner.extract_profile(message, profile, model_factory=model_factory).profile
        actual = profile.model_dump(exclude_none=True)
        assert {
            key: actual.get(key)
            for key in case.expected_fields
        } == case.expected_fields


def test_corpus_schema_forbids_runner_fixture_fields() -> None:
    raw_lines = Path(__file__).with_name("cases.jsonl").read_text(encoding="utf-8").splitlines()
    assert not any("fixture_" in line or "provider_scenario" in line for line in raw_lines)


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


def test_allowed_sources_changes_scoring_not_the_fixture_prediction() -> None:
    original = next(case for case in load_cases(Path(__file__).with_name("cases.jsonl")) if case.id == "P001")
    changed = EvaluationCase(
        original.id, original.category, original.messages, original.expected_intent, original.expected_fields,
        original.expected_action, ["unrelated-source"], original.expected_error, has_trip=original.has_trip,
        expect_schema=original.expect_schema,
    )
    prediction = run_case(original)
    assert run_case(changed) == prediction
    assert score([prediction], [original]).citation_coverage == 1.0
    assert score([prediction], [changed]).citation_coverage == 0.0


def test_baseline_is_the_only_gate_configuration() -> None:
    baseline = load_baseline(Path(__file__).with_name("baseline.json"))
    assert baseline["thresholds"]["schema_validity"] == 0.98
    assert baseline["known_failures"] == []


def test_out_of_range_traveler_fixture_reaches_the_real_extraction_gate() -> None:
    cases = {case.id: case for case in load_cases(Path(__file__).with_name("cases.jsonl"))}
    assert OfflineModel.profile_for(cases["M005"].messages[0])["travelers"] == 0
    assert OfflineModel.profile_for(cases["M006"].messages[0])["travelers"] == 7
    zero = run_case(cases["M005"])
    seven = run_case(cases["M006"])

    assert zero.action == "ask"
    assert zero.error_code == "PROFILE_INVALID"
    assert zero.fields["travelers"] == 0
    assert seven.action == "ask"
    assert seven.error_code == "PROFILE_INVALID"
    assert seven.fields["travelers"] == 7


def test_multiturn_modifications_extract_each_raw_message_in_thread_order(monkeypatch: pytest.MonkeyPatch) -> None:
    cases = {case.id: case for case in load_cases(Path(__file__).with_name("cases.jsonl"))}
    observed: list[tuple[str, dict[str, object]]] = []
    real_extract_profile = runner.extract_profile

    def recording_extract(message: str, profile: TravelProfile, *, model_factory: object) -> TravelProfile:
        observed.append((message, profile.model_dump()))
        return real_extract_profile(message, profile, model_factory=model_factory)

    monkeypatch.setattr(runner, "extract_profile", recording_extract)
    prediction = run_case(cases["M018"])

    assert len(cases["M018"].messages) >= 2
    assert [message for message, _ in observed] == cases["M018"].messages
    assert observed[0][1] == TravelProfile().model_dump()
    assert observed[1][1]["budget_cny"] == 9000
    assert prediction.fields == {
        "origin": "广州",
        "destination": "厦门",
        "start_date": "2026-10-03",
        "end_date": "2026-10-07",
        "travelers": 4,
        "budget_cny": 5000,
        "preferences": ["亲子"],
        "constraints": [],
    }


def test_invalid_field_observation_does_not_leak_into_a_later_unextracted_turn() -> None:
    cases = {case.id: case for case in load_cases(Path(__file__).with_name("cases.jsonl"))}
    case = EvaluationCase(
        id="unit-invalid-field-reset",
        category="unit",
        messages=[cases["M005"].messages[0], cases["R001"].messages[0]],
        expected_intent="plan_trip",
        expected_fields={},
        expected_action="refuse",
        allowed_sources=[],
    )

    prediction = run_case(case)

    assert prediction.action == "refuse"
    assert "travelers" not in prediction.fields


def test_all_multiturn_modifications_finish_from_message_fixtures_not_case_answers() -> None:
    cases = [
        case
        for case in load_cases(Path(__file__).with_name("cases.jsonl"))
        if case.id in {"M016", "M017", "M018", "M019", "M020"}
    ]

    assert all(len(case.messages) >= 2 for case in cases)
    for case in cases:
        prediction = run_case(case)
        assert prediction.action == "modify"
        assert {
            key: prediction.fields.get(key)
            for key in case.expected_fields
        } == case.expected_fields


def test_multiturn_sources_are_selected_from_each_raw_message(monkeypatch: pytest.MonkeyPatch) -> None:
    case = next(
        case
        for case in load_cases(Path(__file__).with_name("cases.jsonl"))
        if case.id == "M016"
    )
    observed: list[str] = []
    real_source_ids_for = runner.source_ids_for

    def recording_source_ids_for(message: str) -> list[str]:
        observed.append(message)
        return real_source_ids_for(message)

    monkeypatch.setattr(runner, "source_ids_for", recording_source_ids_for)
    run_case(case)

    assert observed == case.messages


def test_multiturn_prediction_does_not_depend_on_case_has_trip_hint() -> None:
    case = next(
        case
        for case in load_cases(Path(__file__).with_name("cases.jsonl"))
        if case.id == "M016"
    )

    assert run_case(replace(case, has_trip=False)) == run_case(case)


def test_non_slot_case_is_excluded_by_explicit_applicability() -> None:
    case = EvaluationCase(
        "non-slot", "unit", ["x"], "plan_trip", {"origin": "福州"}, "degrade", [],
        expected_error="PLAN_VALIDATION_FAILED", slot_applicable=False,
    )
    prediction = Prediction(
        "plan_trip", "degrade", {"origin": "杭州"},
        error_code="PLAN_VALIDATION_FAILED", fallback_safe=True,
    )

    report = score([prediction], [case])

    assert report.slot_micro_f1 == 1.0
    assert report.denominators["slot_cases"] == 0
    assert "non-slot" not in report.failures


@pytest.mark.parametrize(
    ("case", "prediction", "reason"),
    [
        (
            EvaluationCase("slot", "unit", ["x"], "plan_trip", {"origin": "上海"}, "plan", []),
            Prediction("plan_trip", "plan", {"origin": "北京"}),
            "slot: expected fields mismatch",
        ),
        (
            EvaluationCase("schema", "unit", ["x"], "plan_trip", {}, "plan", [], expect_schema=True),
            Prediction("plan_trip", "plan", {}, schema_valid=False, budget_valid=True),
            "schema: invalid",
        ),
        (
            EvaluationCase("budget", "unit", ["x"], "plan_trip", {}, "plan", [], expect_schema=True),
            Prediction("plan_trip", "plan", {}, schema_valid=True, budget_valid=False),
            "budget: invalid",
        ),
        (
            EvaluationCase("coverage", "unit", ["x"], "plan_trip", {}, "plan", ["official"], expect_schema=True),
            Prediction("plan_trip", "plan", {}, schema_valid=True, budget_valid=True),
            "citation_coverage: missing allowed source",
        ),
        (
            EvaluationCase("validity", "unit", ["x"], "plan_trip", {}, "plan", []),
            Prediction("plan_trip", "plan", {}, citation_ids=["invented"]),
            "citation_validity: unexpected source",
        ),
        (
            EvaluationCase("unsupported", "unit", ["x"], "plan_trip", {}, "plan", []),
            Prediction("plan_trip", "plan", {}, unsupported_facts=1),
            "unsupported_fact: uncited claim",
        ),
    ],
)
def test_task_success_anti_cheat_reasons_cover_each_quality_gate(
    case: EvaluationCase, prediction: Prediction, reason: str,
) -> None:
    report = score([prediction], [case])

    assert report.task_success_rate == 0.0
    assert reason in report.failures[case.id]


def test_safe_refusal_text_is_not_counted_as_an_unsupported_fact() -> None:
    case = next(
        case
        for case in load_cases(Path(__file__).with_name("cases.jsonl"))
        if case.id == "R002"
    )

    assert run_case(case).unsupported_facts == 0


def test_exception_corpus_covers_each_required_real_component_scenario() -> None:
    cases = [
        case
        for case in load_cases(Path(__file__).with_name("cases.jsonl"))
        if case.category == "exception"
    ]

    assert len(cases) == 10
    assert {SCENARIO_BY_MESSAGE[case.messages[-1]] for case in cases} == {
        "weather_timeout",
        "places_empty_retry",
        "user_limit",
        "global_limit",
        "kill_switch",
        "circuit_open",
        "model_rate_limited",
        "model_upstream_failure",
        "database_failure",
        "format_twice",
    }


def test_global_limit_case_reaches_real_in_memory_reservation_branch(monkeypatch: pytest.MonkeyPatch) -> None:
    case = next(
        case
        for case in load_cases(Path(__file__).with_name("cases.jsonl"))
        if case.id == "E006"
    )
    failure_reasons: list[str | None] = []
    real_reserve = runner.InMemoryUsageRepository.reserve

    def recording_reserve(self: object, *args: object, **kwargs: object):
        result = real_reserve(self, *args, **kwargs)
        failure_reasons.append(result.failure_reason)
        return result

    monkeypatch.setattr(runner.InMemoryUsageRepository, "reserve", recording_reserve)

    prediction = run_case(case)

    assert SCENARIO_BY_MESSAGE[case.messages[-1]] == "global_limit"
    assert failure_reasons == ["global_limit"]
    assert prediction.action == "ask"
    assert prediction.error_code == "AI_GLOBAL_DAILY_LIMIT_REACHED"


@pytest.mark.parametrize(
    ("case_id", "component", "action", "error_code"),
    [
        ("E001", "weather_provider", "degrade", "WEATHER_TIMEOUT"),
        ("E002", "places_provider", "degrade", "PLACES_EMPTY_AFTER_RETRY"),
        ("E003", "model_gateway", "degrade", "AI_CIRCUIT_OPEN"),
        ("E004", "model_gateway", "degrade", "AI_UNAVAILABLE"),
        ("E005", "model_gateway", "degrade", "AI_RATE_LIMITED"),
        ("E006", "usage_guard", "ask", "AI_GLOBAL_DAILY_LIMIT_REACHED"),
        ("E007", "usage_guard", "ask", "AI_DAILY_LIMIT_REACHED"),
        ("E008", "usage_guard", "ask", "AI_DISABLED"),
        ("E009", "planner", "degrade", "PLAN_VALIDATION_FAILED"),
        ("E010", "safe_travel_agent", "degrade", "AGENT_UNAVAILABLE"),
    ],
)
def test_exception_prediction_is_the_target_components_actual_observation(
    case_id: str, component: str, action: str, error_code: str | None,
) -> None:
    case = next(
        case
        for case in load_cases(Path(__file__).with_name("cases.jsonl"))
        if case.id == case_id
    )

    assert hasattr(runner, "observe_scenario")
    observation = runner.observe_scenario(case.messages[-1])

    assert observation.component == component
    assert observation.action == action
    assert observation.error_code == error_code
    assert run_case(case) == observation.to_prediction()


def test_places_empty_retry_observes_two_real_attempts_and_the_provider_degradation() -> None:
    case = next(
        case
        for case in load_cases(Path(__file__).with_name("cases.jsonl"))
        if case.id == "E002"
    )

    assert hasattr(runner, "observe_scenario")
    observation = runner.observe_scenario(case.messages[-1])

    assert observation.attempts == 2
    assert observation.error_code == "PLACES_EMPTY_AFTER_RETRY"


def test_places_empty_result_does_not_synthesize_degradation_from_expected_values() -> None:
    case = next(
        case
        for case in load_cases(Path(__file__).with_name("cases.jsonl"))
        if case.id == "E002"
    )

    observation = runner.observe_scenario(case.messages[-1])
    prediction = run_case(case)
    changed_expectation = replace(
        case,
        expected_action="ask",
        expected_error="INVENTED_EXPECTATION",
    )

    assert observation.action == "degrade"
    assert observation.error_code == "PLACES_EMPTY_AFTER_RETRY"
    assert observation.fallback_safe is True
    assert prediction == observation.to_prediction()
    assert run_case(changed_expectation) == prediction


def test_planner_failure_observes_both_real_repair_attempts() -> None:
    case = next(
        case
        for case in load_cases(Path(__file__).with_name("cases.jsonl"))
        if case.id == "E009"
    )

    assert hasattr(runner, "observe_scenario")
    observation = runner.observe_scenario(case.messages[-1])

    assert observation.attempts == 2
    assert observation.error_code == "PLAN_VALIDATION_FAILED"


def test_non_database_exception_scenarios_do_not_run_the_agent(monkeypatch: pytest.MonkeyPatch) -> None:
    cases = [
        case
        for case in load_cases(Path(__file__).with_name("cases.jsonl"))
        if case.category == "exception" and SCENARIO_BY_MESSAGE[case.messages[-1]] != "database_failure"
    ]

    def unexpected_agent_run(*_: object, **__: object):
        raise AssertionError("component scenario must not run SafeTravelAgent first")

    monkeypatch.setattr(runner.SafeTravelAgent, "run", unexpected_agent_run)

    assert len([run_case(case) for case in cases]) == 9


def test_agent_chat_result_cannot_be_overwritten_by_fixture_side_channels(monkeypatch: pytest.MonkeyPatch) -> None:
    case = next(
        case
        for case in load_cases(Path(__file__).with_name("cases.jsonl"))
        if case.id == "E010"
    )

    class PoisonUsageGuard:
        error_code = "SIDE_CHANNEL_USAGE"

        def __init__(self, _: object) -> None: pass
        def allow(self, _: object) -> bool: return True

    class PoisonEvidenceProvider:
        error_code = "SIDE_CHANNEL_PROVIDER"

        def __init__(self, *_: object) -> None: pass
        def use_message(self, _: str) -> None: pass
        def fetch(self, _: TravelProfile) -> list[object]: return []

    monkeypatch.setattr(runner, "FixtureUsageGuard", PoisonUsageGuard, raising=False)
    monkeypatch.setattr(runner, "FixtureEvidenceProvider", PoisonEvidenceProvider)

    prediction = run_case(case)

    assert prediction.error_code == "AGENT_UNAVAILABLE"


def test_exception_expected_action_and_error_never_change_observation() -> None:
    case = next(
        case
        for case in load_cases(Path(__file__).with_name("cases.jsonl"))
        if case.id == "E001"
    )
    changed = replace(case, expected_action="plan", expected_error="INVENTED_EXPECTATION")

    assert run_case(changed) == run_case(case)


def test_production_composition_flow_covers_plan_modify_explain_and_reopen() -> None:
    report = runner.run_production_composition_evaluation()

    assert report.mode == "production_composition_offline_seams"
    assert report.harness_version == "production-flow-v2"
    assert [step.name for step in report.steps] == [
        "smalltalk",
        "unsupported",
        "plan_and_save",
        "modify_and_save",
        "explain",
        "reopen",
    ]
    assert all(step.success for step in report.steps)
    assert report.success_rate == 1.0
    assert 0 <= report.p50_latency_ms <= report.p95_latency_ms
    assert report.model_calls == 0
    assert report.input_tokens == 0
    assert report.output_tokens == 0
    assert report.estimated_cost_micros == 0
    assert "offline" in report.cost_basis
    assert "RuleIntentClassifier" in report.production_components
    assert "RuleTravelExtractor" in report.production_components
    assert report.change_summary


def test_written_report_fails_closed_when_production_composition_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = EvaluationCase(
        "unit-production-gate", "unit", ["x"], "plan_trip", {}, "ask", []
    )
    component_report = score([Prediction("plan_trip", "ask", {})], [case])
    production_report = runner.run_production_composition_evaluation()
    monkeypatch.setattr(
        runner,
        "run_production_composition_evaluation",
        lambda: replace(production_report, success_rate=0.5),
    )

    assert runner._write_report(component_report, tmp_path, {}, []) is False
    payload = __import__("json").loads(
        (tmp_path / "evaluation-report.json").read_text(encoding="utf-8")
    )
    assert "production_composition_success" in payload["failed_thresholds"]


def test_written_report_labels_fixture_gate_and_embeds_production_flow(tmp_path: Path) -> None:
    case = EvaluationCase(
        "unit-report", "unit", ["x"], "plan_trip", {}, "ask", []
    )
    report = score([Prediction("plan_trip", "ask", {})], [case])

    assert runner._write_report(report, tmp_path, {}, []) is True

    payload = __import__("json").loads(
        (tmp_path / "evaluation-report.json").read_text(encoding="utf-8")
    )
    markdown = (tmp_path / "evaluation-report.md").read_text(encoding="utf-8")
    assert payload["evaluation_mode"] == "offline_component_fixtures"
    assert payload["harness_version"] == "offline-components-v2"
    assert payload["production_composition"]["success_rate"] == 1.0
    assert "production composition" in markdown.lower()
    assert "not a paid-model or network benchmark" in markdown.lower()
