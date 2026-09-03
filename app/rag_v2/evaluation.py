from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import re
from typing import Sequence
from urllib.parse import urlparse
from uuid import UUID

from app.rag_v2.retrieval import RetrievalEvidence, RetrievalResult


_DESTINATION_CODE_PATTERN = re.compile(r"\d{6}")
_PASSED = "passed"
_NO_ANSWER_MISMATCH = "unexpected evidence"
_DESTINATION_MISMATCH = "destination mismatch"
_MISSING_ATTRACTION = "missing expected attraction"
_MISSING_CHUNK = "missing expected chunk"
_DUPLICATE_CHUNK_KEY = "duplicate chunk key"
_DUPLICATE_CONTENT_HASH = "duplicate content hash"
_INCOMPLETE_SOURCE = "incomplete source"


@dataclass(frozen=True)
class EvaluationCase:
    case_id: str
    query: str
    expected_destination_code: str | None
    attraction_destinations: tuple[tuple[UUID, str], ...]
    expected_attraction_ids: tuple[UUID, ...]
    expected_chunk_keys: tuple[str, ...]
    expect_no_answer: bool
    require_source_urls: bool = True

    def __post_init__(self) -> None:
        attraction_ids: set[UUID] = set()
        for attraction_id, destination_code in self.attraction_destinations:
            if attraction_id in attraction_ids:
                raise ValueError("attraction destination keys must be unique")
            attraction_ids.add(attraction_id)
            if (
                not isinstance(destination_code, str)
                or _DESTINATION_CODE_PATTERN.fullmatch(destination_code) is None
            ):
                raise ValueError("destination codes must be six digits")


@dataclass(frozen=True)
class EvaluationObservation:
    case_id: str
    passed: bool
    reason: str
    returned_attraction_ids: tuple[UUID, ...]
    returned_chunk_keys: tuple[str, ...]


def evaluate_case(
    *,
    case: EvaluationCase,
    result: RetrievalResult,
) -> EvaluationObservation:
    evidence = result.evidence
    returned_attraction_ids = tuple(item.attraction_id for item in evidence)
    returned_chunk_keys = tuple(item.chunk_key for item in evidence)

    if case.expect_no_answer and evidence:
        return _observation(
            case,
            returned_attraction_ids,
            returned_chunk_keys,
            _NO_ANSWER_MISMATCH,
        )

    if case.expected_destination_code is not None:
        destinations = dict(case.attraction_destinations)
        if any(
            destinations.get(item.attraction_id) != case.expected_destination_code
            for item in evidence
        ):
            return _observation(
                case,
                returned_attraction_ids,
                returned_chunk_keys,
                _DESTINATION_MISMATCH,
            )

    returned_attraction_id_set = set(returned_attraction_ids)
    if not set(case.expected_attraction_ids).issubset(returned_attraction_id_set):
        return _observation(
            case,
            returned_attraction_ids,
            returned_chunk_keys,
            _MISSING_ATTRACTION,
        )

    returned_chunk_key_set = set(returned_chunk_keys)
    if not set(case.expected_chunk_keys).issubset(returned_chunk_key_set):
        return _observation(
            case,
            returned_attraction_ids,
            returned_chunk_keys,
            _MISSING_CHUNK,
        )

    if len(returned_chunk_keys) != len(returned_chunk_key_set):
        return _observation(
            case,
            returned_attraction_ids,
            returned_chunk_keys,
            _DUPLICATE_CHUNK_KEY,
        )

    content_hashes = tuple(item.content_hash for item in evidence)
    if len(content_hashes) != len(set(content_hashes)):
        return _observation(
            case,
            returned_attraction_ids,
            returned_chunk_keys,
            _DUPLICATE_CONTENT_HASH,
        )

    if case.require_source_urls and any(
        not _has_complete_source(item) for item in evidence
    ):
        return _observation(
            case,
            returned_attraction_ids,
            returned_chunk_keys,
            _INCOMPLETE_SOURCE,
        )

    return EvaluationObservation(
        case_id=case.case_id,
        passed=True,
        reason=_PASSED,
        returned_attraction_ids=returned_attraction_ids,
        returned_chunk_keys=returned_chunk_keys,
    )


def evaluate_cases(
    cases: Sequence[EvaluationCase],
    results: Sequence[RetrievalResult],
) -> tuple[EvaluationObservation, ...]:
    if len(cases) != len(results):
        raise ValueError("cases and results must have the same length")
    return tuple(
        evaluate_case(case=case, result=result)
        for case, result in zip(cases, results, strict=True)
    )


def _observation(
    case: EvaluationCase,
    returned_attraction_ids: tuple[UUID, ...],
    returned_chunk_keys: tuple[str, ...],
    reason: str,
) -> EvaluationObservation:
    return EvaluationObservation(
        case_id=case.case_id,
        passed=False,
        reason=reason,
        returned_attraction_ids=returned_attraction_ids,
        returned_chunk_keys=returned_chunk_keys,
    )


def _has_complete_source(evidence: RetrievalEvidence) -> bool:
    if (
        not isinstance(evidence.source_label, str)
        or not evidence.source_label.strip()
        or not isinstance(evidence.source_type, str)
        or not evidence.source_type.strip()
        or not isinstance(evidence.reviewed_on, date)
        or not isinstance(evidence.source_url, str)
    ):
        return False

    try:
        parsed = urlparse(evidence.source_url)
        hostname = parsed.hostname
    except ValueError:
        return False

    return (
        parsed.scheme == "https"
        and bool(hostname)
        and parsed.username is None
        and parsed.password is None
    )
