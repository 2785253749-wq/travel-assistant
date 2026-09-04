from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Sequence
from uuid import UUID

from app.rag_v2.evaluation import (
    EvaluationCase,
    EvaluationObservation,
    evaluate_case,
)
from app.rag_v2.retrieval import RetrievalResult


MatchMode = Literal["exact", "attraction", "no-answer"]

_CASE_FIELDS = frozenset(
    {
        "case_id",
        "required",
        "match_mode",
        "query",
        "expected_destination_code",
        "attraction_destinations",
        "expected_attraction_ids",
        "expected_chunk_keys",
        "expect_no_answer",
        "require_source_urls",
    }
)
_MATCH_MODES = frozenset({"exact", "attraction", "no-answer"})
_DESTINATION_CODE = re.compile(r"\d{6}")


@dataclass(frozen=True)
class AcceptanceCase:
    case_id: str
    required: bool
    match_mode: MatchMode
    query: str
    expected_destination_code: str | None
    attraction_destinations: tuple[tuple[UUID, str], ...]
    expected_attraction_ids: tuple[UUID, ...]
    expected_chunk_keys: tuple[str, ...]
    expect_no_answer: bool
    require_source_urls: bool

    def __post_init__(self) -> None:
        _validate_case(self)


@dataclass(frozen=True)
class AcceptanceObservation:
    case: AcceptanceCase
    evaluation: EvaluationObservation


def load_acceptance_cases(path: Path) -> tuple[AcceptanceCase, ...]:
    cases: list[AcceptanceCase] = []
    case_ids: set[str] = set()

    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ValueError("could not read acceptance case file") from exc

    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid JSONL at line {line_number}") from exc
        if not isinstance(raw, dict):
            raise ValueError(f"acceptance case at line {line_number} must be an object")

        unknown_fields = set(raw) - _CASE_FIELDS
        missing_fields = _CASE_FIELDS - set(raw)
        if unknown_fields:
            raise ValueError("acceptance case contains unknown fields")
        if missing_fields:
            raise ValueError("acceptance case is missing required fields")

        case = _case_from_raw(raw)
        if case.case_id in case_ids:
            raise ValueError("acceptance case IDs must be unique")
        case_ids.add(case.case_id)
        cases.append(case)

    return tuple(cases)


def case_file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def evaluate_acceptance_case(
    *,
    case: AcceptanceCase,
    result: RetrievalResult,
) -> AcceptanceObservation:
    expected_chunk_keys = (
        case.expected_chunk_keys if case.match_mode == "exact" else ()
    )
    evaluation_case = EvaluationCase(
        case_id=case.case_id,
        query=case.query,
        expected_destination_code=case.expected_destination_code,
        attraction_destinations=case.attraction_destinations,
        expected_attraction_ids=case.expected_attraction_ids,
        expected_chunk_keys=expected_chunk_keys,
        expect_no_answer=case.match_mode == "no-answer",
        require_source_urls=case.require_source_urls,
    )
    return AcceptanceObservation(
        case=case,
        evaluation=evaluate_case(case=evaluation_case, result=result),
    )


def required_cases_pass(observations: Sequence[AcceptanceObservation]) -> bool:
    return all(
        observation.evaluation.passed
        for observation in observations
        if observation.case.required
    )


def _case_from_raw(raw: dict[str, Any]) -> AcceptanceCase:
    case_id = _require_nonempty_string(raw["case_id"], "case_id")
    query = _require_nonempty_string(raw["query"], "query")
    match_mode = raw["match_mode"]
    if match_mode not in _MATCH_MODES:
        raise ValueError("acceptance case match_mode is invalid")

    expected_destination_code = _parse_destination_code(
        raw["expected_destination_code"],
        "expected_destination_code",
        allow_none=True,
    )
    attraction_destinations = _parse_attraction_destinations(
        raw["attraction_destinations"]
    )
    expected_attraction_ids = _parse_uuid_list(
        raw["expected_attraction_ids"], "expected_attraction_ids"
    )
    expected_chunk_keys = _parse_string_list(
        raw["expected_chunk_keys"], "expected_chunk_keys"
    )

    return AcceptanceCase(
        case_id=case_id,
        required=_require_bool(raw["required"], "required"),
        match_mode=match_mode,
        query=query,
        expected_destination_code=expected_destination_code,
        attraction_destinations=attraction_destinations,
        expected_attraction_ids=expected_attraction_ids,
        expected_chunk_keys=expected_chunk_keys,
        expect_no_answer=_require_bool(raw["expect_no_answer"], "expect_no_answer"),
        require_source_urls=_require_bool(
            raw["require_source_urls"], "require_source_urls"
        ),
    )


def _validate_case(case: AcceptanceCase) -> None:
    if not isinstance(case.case_id, str) or not case.case_id.strip():
        raise ValueError("case_id must be a non-empty string")
    if not isinstance(case.query, str) or not case.query.strip():
        raise ValueError("query must be a non-empty string")
    if not isinstance(case.required, bool):
        raise ValueError("required must be boolean")
    if case.match_mode not in _MATCH_MODES:
        raise ValueError("acceptance case match_mode is invalid")
    if not isinstance(case.expect_no_answer, bool):
        raise ValueError("expect_no_answer must be boolean")
    if not isinstance(case.require_source_urls, bool):
        raise ValueError("require_source_urls must be boolean")

    _parse_destination_code(
        case.expected_destination_code,
        "expected_destination_code",
        allow_none=True,
    )
    _validate_attraction_destinations(case.attraction_destinations)
    if any(not isinstance(value, UUID) for value in case.expected_attraction_ids):
        raise ValueError("expected attraction IDs must be UUIDs")
    if len(case.expected_attraction_ids) != len(set(case.expected_attraction_ids)):
        raise ValueError("expected attraction IDs must be unique")
    if any(
        not isinstance(value, str) or not value.strip()
        for value in case.expected_chunk_keys
    ):
        raise ValueError("expected chunk keys must be non-empty strings")
    if len(case.expected_chunk_keys) != len(set(case.expected_chunk_keys)):
        raise ValueError("expected chunk keys must be unique")

    if case.match_mode == "exact":
        if (
            case.expected_destination_code is None
            or not case.expected_attraction_ids
            or not case.expected_chunk_keys
            or case.expect_no_answer
        ):
            raise ValueError(
                "exact cases require destination, attraction, chunks, and evidence"
            )
    elif case.match_mode == "attraction":
        if not case.expected_attraction_ids or case.expected_chunk_keys or case.expect_no_answer:
            raise ValueError("attraction cases require an attraction without a fixed chunk")
    elif (
        case.expected_destination_code is not None
        or case.attraction_destinations
        or case.expected_attraction_ids
        or case.expected_chunk_keys
        or not case.expect_no_answer
    ):
        raise ValueError("no-answer cases require empty expectations")


def _validate_attraction_destinations(
    values: tuple[tuple[UUID, str], ...],
) -> None:
    attraction_ids: set[UUID] = set()
    for attraction_id, destination_code in values:
        if not isinstance(attraction_id, UUID):
            raise ValueError("attraction mapping IDs must be UUIDs")
        if attraction_id in attraction_ids:
            raise ValueError("attraction mapping IDs must be unique")
        attraction_ids.add(attraction_id)
        _parse_destination_code(destination_code, "attraction destination code")


def _parse_attraction_destinations(
    value: object,
) -> tuple[tuple[UUID, str], ...]:
    if not isinstance(value, list):
        raise ValueError("attraction_destinations must be a list")
    mappings: list[tuple[UUID, str]] = []
    for item in value:
        if not isinstance(item, list) or len(item) != 2:
            raise ValueError("attraction mappings must be UUID/code pairs")
        attraction_id = _parse_uuid(item[0], "attraction mapping ID")
        destination_code = _parse_destination_code(
            item[1], "attraction destination code"
        )
        mappings.append((attraction_id, destination_code))
    return tuple(mappings)


def _parse_uuid_list(value: object, field_name: str) -> tuple[UUID, ...]:
    if not isinstance(value, list):
        raise ValueError(f"{field_name} must be a list")
    return tuple(_parse_uuid(item, field_name) for item in value)


def _parse_string_list(value: object, field_name: str) -> tuple[str, ...]:
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item.strip() for item in value
    ):
        raise ValueError(f"{field_name} must be a list of non-empty strings")
    return tuple(value)


def _parse_uuid(value: object, field_name: str) -> UUID:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a UUID string")
    try:
        return UUID(value)
    except ValueError as exc:
        raise ValueError(f"{field_name} must be a UUID string") from exc


def _parse_destination_code(
    value: object,
    field_name: str,
    *,
    allow_none: bool = False,
) -> str | None:
    if allow_none and value is None:
        return None
    if not isinstance(value, str) or _DESTINATION_CODE.fullmatch(value) is None:
        raise ValueError(f"{field_name} must be six digits")
    return value


def _require_nonempty_string(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return value


def _require_bool(value: object, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{field_name} must be boolean")
    return value
