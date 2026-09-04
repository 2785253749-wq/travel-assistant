from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path
from uuid import UUID

import pytest

from app.rag_v2.acceptance_cases import (
    AcceptanceCase,
    AcceptanceObservation,
    MatchMode,
    case_file_sha256,
    evaluate_acceptance_case,
    load_acceptance_cases,
    required_cases_pass,
)
from app.rag_v2.evaluation import EvaluationObservation
from app.rag_v2.models import ChunkType
from app.rag_v2.retrieval import RetrievalEvidence, RetrievalResult


_ATTRACTION_A = UUID("00000000-0000-4000-8000-000000000101")
_ATTRACTION_B = UUID("00000000-0000-4000-8000-000000000102")
_EXACT_CHUNK = f"rag-v2-chunk-key-v1|{_ATTRACTION_A}|overview|0"


def _raw_case(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "case_id": "xiamen-gulangyu-exact",
        "required": True,
        "match_mode": "exact",
        "query": "厦门鼓浪屿有哪些亮点？",
        "expected_destination_code": "350200",
        "attraction_destinations": [
            [str(_ATTRACTION_A), "350200"],
            [str(_ATTRACTION_B), "350100"],
        ],
        "expected_attraction_ids": [str(_ATTRACTION_A)],
        "expected_chunk_keys": [_EXACT_CHUNK],
        "expect_no_answer": False,
        "require_source_urls": True,
    }
    values.update(overrides)
    return values


def _write_jsonl(path: Path, *records: dict[str, object]) -> Path:
    path.write_text(
        "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records),
        encoding="utf-8",
    )
    return path


def _case(**overrides: object) -> AcceptanceCase:
    values: dict[str, object] = {
        "case_id": "xiamen-gulangyu-exact",
        "required": True,
        "match_mode": "exact",
        "query": "厦门鼓浪屿有哪些亮点？",
        "expected_destination_code": "350200",
        "attraction_destinations": (
            (_ATTRACTION_A, "350200"),
            (_ATTRACTION_B, "350100"),
        ),
        "expected_attraction_ids": (_ATTRACTION_A,),
        "expected_chunk_keys": (_EXACT_CHUNK,),
        "expect_no_answer": False,
        "require_source_urls": True,
    }
    values.update(overrides)
    return AcceptanceCase(**values)


def _evidence(**overrides: object) -> RetrievalEvidence:
    values: dict[str, object] = {
        "attraction_id": _ATTRACTION_A,
        "chunk_key": _EXACT_CHUNK,
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


def _result(*evidence: RetrievalEvidence) -> RetrievalResult:
    return RetrievalResult(query="厦门鼓浪屿有哪些亮点？", evidence=evidence)


def _acceptance_observation(
    case: AcceptanceCase,
    *,
    passed: bool,
) -> AcceptanceObservation:
    return AcceptanceObservation(
        case=case,
        evaluation=EvaluationObservation(
            case_id=case.case_id,
            passed=passed,
            reason="passed" if passed else "fixture failure",
            returned_attraction_ids=(),
            returned_chunk_keys=(),
        ),
    )


def test_case_parser_maps_all_fields(tmp_path: Path) -> None:
    cases = load_acceptance_cases(_write_jsonl(tmp_path / "cases-v1.jsonl", _raw_case()))

    assert len(cases) == 1
    case = cases[0]
    assert case.case_id == "xiamen-gulangyu-exact"
    assert case.required is True
    assert isinstance(case.match_mode, str)
    assert case.match_mode == "exact"
    assert case.query == "厦门鼓浪屿有哪些亮点？"
    assert case.expected_destination_code == "350200"
    assert case.attraction_destinations == (
        (_ATTRACTION_A, "350200"),
        (_ATTRACTION_B, "350100"),
    )
    assert case.expected_attraction_ids == (_ATTRACTION_A,)
    assert case.expected_chunk_keys == (_EXACT_CHUNK,)
    assert case.expect_no_answer is False
    assert case.require_source_urls is True


def test_case_parser_rejects_unknown_match_mode(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        load_acceptance_cases(
            _write_jsonl(tmp_path / "invalid.jsonl", _raw_case(match_mode="fuzzy"))
        )


def test_case_parser_rejects_duplicate_case_id(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        load_acceptance_cases(
            _write_jsonl(
                tmp_path / "duplicates.jsonl",
                _raw_case(),
                _raw_case(query="同一个稳定 ID"),
            )
        )


def test_case_parser_rejects_invalid_destination_mapping(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        load_acceptance_cases(
            _write_jsonl(
                tmp_path / "invalid-destination.jsonl",
                _raw_case(attraction_destinations=[[str(_ATTRACTION_A), "35020"]]),
            )
        )


def test_case_parser_rejects_malformed_jsonl(tmp_path: Path) -> None:
    path = tmp_path / "malformed.jsonl"
    path.write_text('{"case_id": "valid"}\nnot-json\n', encoding="utf-8")

    with pytest.raises(ValueError):
        load_acceptance_cases(path)


def test_case_file_hash_is_sha256_of_bytes(tmp_path: Path) -> None:
    path = tmp_path / "cases-v1.jsonl"
    payload = b'{"case_id":"bytes-are-authoritative"}\n'
    path.write_bytes(payload)

    assert case_file_sha256(path) == hashlib.sha256(payload).hexdigest()


def test_exact_mode_requires_destination_expectation() -> None:
    with pytest.raises(ValueError):
        _case(expected_destination_code=None)


def test_exact_mode_requires_attraction_expectation() -> None:
    with pytest.raises(ValueError):
        _case(expected_attraction_ids=())


def test_exact_mode_requires_expected_chunk() -> None:
    case = _case(match_mode="exact", expected_chunk_keys=(_EXACT_CHUNK,))

    observation = evaluate_acceptance_case(
        case=case,
        result=_result(
            _evidence(
                chunk_key=f"rag-v2-chunk-key-v1|{_ATTRACTION_A}|highlights|0",
                chunk_type=ChunkType.highlights,
            )
        ),
    )

    assert observation.evaluation.passed is False
    assert observation.evaluation.reason == "missing expected chunk"


def test_attraction_mode_does_not_require_fixed_chunk() -> None:
    case = _case(
        match_mode="attraction",
        expected_chunk_keys=(),
    )
    result = _result(_evidence(chunk_key="rag-v2-chunk-key-v1|other|transport|0"))

    observation = evaluate_acceptance_case(case=case, result=result)

    assert observation.evaluation.passed is True


def test_no_answer_mode_requires_empty_evidence() -> None:
    case = _case(
        match_mode="no-answer",
        expected_destination_code=None,
        attraction_destinations=(),
        expected_attraction_ids=(),
        expected_chunk_keys=(),
        expect_no_answer=True,
    )

    empty_observation = evaluate_acceptance_case(case=case, result=_result())
    evidence_observation = evaluate_acceptance_case(
        case=case,
        result=_result(_evidence()),
    )

    assert empty_observation.evaluation.passed is True
    assert evidence_observation.evaluation.passed is False


def test_required_case_gate_fails_on_required_observation() -> None:
    required_case = _case(case_id="required", required=True)
    optional_case = _case(case_id="optional", required=False)

    observations = (
        _acceptance_observation(required_case, passed=False),
        _acceptance_observation(optional_case, passed=True),
    )

    assert required_cases_pass(observations) is False


def test_optional_failure_does_not_fail_required_gate() -> None:
    required_case = _case(case_id="required", required=True)
    optional_case = _case(case_id="optional", required=False)

    observations = (
        _acceptance_observation(required_case, passed=True),
        _acceptance_observation(optional_case, passed=False),
    )

    assert required_cases_pass(observations) is True


def test_wrapper_delegates_source_rules_to_existing_evaluator() -> None:
    case = _case(require_source_urls=True)
    result = _result(_evidence(source_url="http://example.test/not-https"))

    observation = evaluate_acceptance_case(case=case, result=result)

    assert observation.evaluation.passed is False
    assert observation.evaluation.reason == "incomplete source"
