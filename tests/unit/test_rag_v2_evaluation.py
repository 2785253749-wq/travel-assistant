from __future__ import annotations

import ast
from dataclasses import FrozenInstanceError, fields
from datetime import date
from inspect import signature
from pathlib import Path
from typing import Sequence, get_type_hints
from uuid import UUID

import pytest

from app.rag_v2.evaluation import (
    EvaluationCase,
    EvaluationObservation,
    evaluate_case,
    evaluate_cases,
)
from app.rag_v2.models import ChunkType
from app.rag_v2.retrieval import RetrievalEvidence, RetrievalResult


_ATTRACTION_A = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
_ATTRACTION_B = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
_ATTRACTION_C = UUID("cccccccc-cccc-cccc-cccc-cccccccccccc")


def _evidence(**overrides: object) -> RetrievalEvidence:
    values: dict[str, object] = {
        "attraction_id": _ATTRACTION_A,
        "chunk_key": "a-overview-0",
        "chunk_type": ChunkType.overview,
        "content": "鼓浪屿位于厦门。",
        "content_hash": "a" * 64,
        "source_label": "厦门市文化和旅游局",
        "source_url": "https://example.test/gulangyu",
        "source_type": "official",
        "reviewed_on": date(2026, 9, 3),
        "score": 0.95,
    }
    values.update(overrides)
    return RetrievalEvidence(**values)


def _result(*evidence: RetrievalEvidence, query: str = "厦门日落") -> RetrievalResult:
    return RetrievalResult(query=query, evidence=evidence)


def _case(**overrides: object) -> EvaluationCase:
    values: dict[str, object] = {
        "case_id": "gulanyu-sunset",
        "query": "厦门日落",
        "expected_destination_code": "350200",
        "attraction_destinations": (
            (_ATTRACTION_A, "350200"),
            (_ATTRACTION_B, "350100"),
            (_ATTRACTION_C, "350300"),
        ),
        "expected_attraction_ids": (),
        "expected_chunk_keys": (),
        "expect_no_answer": False,
        "require_source_urls": True,
    }
    values.update(overrides)
    return EvaluationCase(**values)


def test_evaluation_module_exposes_exact_frozen_public_api() -> None:
    assert tuple(field.name for field in fields(EvaluationCase)) == (
        "case_id",
        "query",
        "expected_destination_code",
        "attraction_destinations",
        "expected_attraction_ids",
        "expected_chunk_keys",
        "expect_no_answer",
        "require_source_urls",
    )
    assert tuple(field.name for field in fields(EvaluationObservation)) == (
        "case_id",
        "passed",
        "reason",
        "returned_attraction_ids",
        "returned_chunk_keys",
    )
    assert tuple(signature(evaluate_case).parameters) == ("case", "result")
    assert all(
        parameter.kind.name == "KEYWORD_ONLY"
        for parameter in signature(evaluate_case).parameters.values()
    )
    assert tuple(signature(evaluate_cases).parameters) == ("cases", "results")
    assert get_type_hints(EvaluationCase) == {
        "case_id": str,
        "query": str,
        "expected_destination_code": str | None,
        "attraction_destinations": tuple[tuple[UUID, str], ...],
        "expected_attraction_ids": tuple[UUID, ...],
        "expected_chunk_keys": tuple[str, ...],
        "expect_no_answer": bool,
        "require_source_urls": bool,
    }
    assert get_type_hints(EvaluationObservation) == {
        "case_id": str,
        "passed": bool,
        "reason": str,
        "returned_attraction_ids": tuple[UUID, ...],
        "returned_chunk_keys": tuple[str, ...],
    }
    assert get_type_hints(evaluate_case) == {
        "case": EvaluationCase,
        "result": RetrievalResult,
        "return": EvaluationObservation,
    }
    assert get_type_hints(evaluate_cases) == {
        "cases": Sequence[EvaluationCase],
        "results": Sequence[RetrievalResult],
        "return": tuple[EvaluationObservation, ...],
    }

    with pytest.raises(FrozenInstanceError):
        _case().case_id = "changed"
    with pytest.raises(FrozenInstanceError):
        evaluate_case(case=_case(), result=_result()).passed = False


@pytest.mark.parametrize(
    "attraction_destinations",
    [
        ((_ATTRACTION_A, "350200"), (_ATTRACTION_A, "350100")),
        ((_ATTRACTION_A, "invalid"),),
    ],
)
def test_evaluation_case_rejects_duplicate_mapping_keys_and_invalid_codes(
    attraction_destinations: tuple[tuple[UUID, str], ...],
) -> None:
    with pytest.raises(ValueError):
        _case(attraction_destinations=attraction_destinations)


def test_evaluate_case_returns_complete_passing_observation() -> None:
    evidence = _evidence()

    observation = evaluate_case(
        case=_case(
            expected_attraction_ids=(_ATTRACTION_A,),
            expected_chunk_keys=("a-overview-0",),
        ),
        result=_result(evidence),
    )

    assert observation.case_id == "gulanyu-sunset"
    assert observation.passed is True
    assert observation.reason
    assert observation.returned_attraction_ids == (_ATTRACTION_A,)
    assert observation.returned_chunk_keys == ("a-overview-0",)


def test_evaluate_cases_pairs_inputs_positionally_and_preserves_case_order() -> None:
    first_case = _case(case_id="first", query="first query")
    second_case = _case(
        case_id="second",
        query="second query",
        expected_destination_code="350100",
    )
    first_result = _result(_evidence(attraction_id=_ATTRACTION_A), query="second query")
    second_result = _result(
        _evidence(
            attraction_id=_ATTRACTION_B,
            chunk_key="b-overview-0",
            content_hash="b" * 64,
        ),
        query="first query",
    )

    observations = evaluate_cases(
        (first_case, second_case),
        (first_result, second_result),
    )

    assert tuple(observation.case_id for observation in observations) == (
        "first",
        "second",
    )
    assert tuple(observation.passed for observation in observations) == (True, True)


@pytest.mark.parametrize(
    ("cases", "results"),
    [
        ((_case(), _case(case_id="second")), (_result(),)),
        ((_case(),), (_result(), _result(query="another result"))),
    ],
)
def test_evaluate_cases_rejects_non_matching_input_lengths(
    cases: tuple[EvaluationCase, ...],
    results: tuple[RetrievalResult, ...],
) -> None:
    with pytest.raises(ValueError, match="^cases and results must have the same length$"):
        evaluate_cases(cases, results)


@pytest.mark.parametrize(
    ("result", "passed"),
    [
        (_result(), True),
        (_result(_evidence()), False),
    ],
)
def test_no_answer_cases_pass_if_and_only_if_evidence_is_empty(
    result: RetrievalResult,
    passed: bool,
) -> None:
    observation = evaluate_case(
        case=_case(expect_no_answer=True),
        result=result,
    )

    assert observation.passed is passed


def test_destination_check_is_disabled_when_no_destination_is_expected() -> None:
    observation = evaluate_case(
        case=_case(expected_destination_code=None, attraction_destinations=()),
        result=_result(_evidence(attraction_id=_ATTRACTION_A)),
    )

    assert observation.passed is True


@pytest.mark.parametrize(
    "evidence",
    [
        _evidence(attraction_id=_ATTRACTION_B),
        _evidence(attraction_id=UUID("dddddddd-dddd-dddd-dddd-dddddddddddd")),
        (
            _evidence(attraction_id=_ATTRACTION_A),
            _evidence(
                attraction_id=_ATTRACTION_B,
                chunk_key="b-overview-0",
                content_hash="b" * 64,
            ),
        ),
    ],
)
def test_enabled_destination_check_rejects_wrong_unknown_and_mixed_destinations(
    evidence: RetrievalEvidence | tuple[RetrievalEvidence, ...],
) -> None:
    actual_evidence = evidence if isinstance(evidence, tuple) else (evidence,)

    observation = evaluate_case(case=_case(), result=_result(*actual_evidence))

    assert observation.passed is False


@pytest.mark.parametrize(
    ("expected_ids", "evidence", "passed"),
    [
        ((_ATTRACTION_A, _ATTRACTION_B), (_evidence(), _evidence(
            attraction_id=_ATTRACTION_B,
            chunk_key="b-overview-0",
            content_hash="b" * 64,
        )), True),
        ((_ATTRACTION_A, _ATTRACTION_B), (_evidence(),), False),
        ((_ATTRACTION_A, _ATTRACTION_B), (_evidence(), _evidence(
            attraction_id=_ATTRACTION_C,
            chunk_key="c-overview-0",
            content_hash="c" * 64,
        )), False),
    ],
)
def test_expected_attraction_ids_require_each_expected_identity(
    expected_ids: tuple[UUID, ...],
    evidence: tuple[RetrievalEvidence, ...],
    passed: bool,
) -> None:
    observation = evaluate_case(
        case=_case(
            expected_destination_code=None,
            expected_attraction_ids=expected_ids,
        ),
        result=_result(*evidence),
    )

    assert observation.passed is passed


@pytest.mark.parametrize(
    ("expected_chunk_keys", "evidence", "passed"),
    [
        (("a-overview-0", "b-overview-0"), (_evidence(), _evidence(
            attraction_id=_ATTRACTION_B,
            chunk_key="b-overview-0",
            content_hash="b" * 64,
        )), True),
        (("a-overview-0", "b-overview-0"), (_evidence(),), False),
    ],
)
def test_expected_chunk_keys_require_each_expected_chunk(
    expected_chunk_keys: tuple[str, ...],
    evidence: tuple[RetrievalEvidence, ...],
    passed: bool,
) -> None:
    observation = evaluate_case(
        case=_case(
            expected_destination_code=None,
            expected_chunk_keys=expected_chunk_keys,
        ),
        result=_result(*evidence),
    )

    assert observation.passed is passed


def test_duplicate_chunk_keys_fail_even_when_content_hashes_differ() -> None:
    observation = evaluate_case(
        case=_case(),
        result=_result(
            _evidence(),
            _evidence(content_hash="b" * 64),
        ),
    )

    assert observation.passed is False


def test_duplicate_content_hashes_fail_even_when_chunk_keys_differ() -> None:
    observation = evaluate_case(
        case=_case(),
        result=_result(
            _evidence(),
            _evidence(chunk_key="a-highlights-0"),
        ),
    )

    assert observation.passed is False


@pytest.mark.parametrize(
    "overrides",
    [
        {"source_label": "  "},
        {"source_type": "  "},
        {"reviewed_on": None},
        {"source_url": "/relative"},
        {"source_url": "http://example.test/source"},
        {"source_url": "https:///missing-host"},
        {"source_url": "https://user@example.test/source"},
        {"source_url": "https://:secret@example.test/source"},
    ],
)
def test_required_source_completeness_rejects_each_invalid_provenance_field(
    overrides: dict[str, object],
) -> None:
    observation = evaluate_case(
        case=_case(require_source_urls=True),
        result=_result(_evidence(**overrides)),
    )

    assert observation.passed is False


def test_source_completeness_is_disabled_only_when_the_case_requests_it() -> None:
    observation = evaluate_case(
        case=_case(require_source_urls=False),
        result=_result(_evidence(source_url="http://example.test/source")),
    )

    assert observation.passed is True


def test_empty_evidence_cannot_satisfy_normal_expected_attraction_or_chunk_checks() -> None:
    observation = evaluate_case(
        case=_case(
            expected_attraction_ids=(_ATTRACTION_A,),
            expected_chunk_keys=("a-overview-0",),
        ),
        result=_result(),
    )

    assert observation.passed is False


def test_identical_inputs_produce_equal_observations() -> None:
    case = _case(expected_attraction_ids=(_ATTRACTION_A,))
    result = _result(_evidence())

    assert evaluate_case(case=case, result=result) == evaluate_case(
        case=case,
        result=result,
    )


def test_evaluator_module_has_no_network_database_or_runtime_dependencies() -> None:
    project_root = Path(__file__).resolve().parents[2]
    evaluation_path = project_root / "app" / "rag_v2" / "evaluation.py"
    tree = ast.parse(evaluation_path.read_text(encoding="utf-8"))

    imported_modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported_modules.append(node.module)

    forbidden_module_roots = (
        "supabase",
        "postgrest",
        "pgvector",
        "httpx",
        "jina",
        "app.agent",
        "app.composition",
        "app.infrastructure",
        "app.planner",
        "app.rag",
        "app.runtime",
        "app.rag_v2.embedding",
        "app.rag_v2.importer",
        "app.rag_v2.passage_embedding",
        "app.rag_v2.repository",
    )

    assert [
        module
        for module in imported_modules
        if any(module == root or module.startswith(f"{root}.") for root in forbidden_module_roots)
    ] == []
