"""Structured itinerary generation and deterministic safety validation."""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import date
from typing import Any

from pydantic import ValidationError

from app.agent.graph import TrustedEvidence
from app.providers.base import ProviderResult
from app.schemas import Itinerary, SourceCitation, TravelProfile


_TRUSTED_SOURCE_TYPES = {"official", "government", "trusted_provider"}
_VARIABLE_FACT = re.compile(
    r"(?:live\s+(?:price|availability|inventory)|real[ -]?time|per\s+night|"
    r"价格|票价|库存|余票|营业时间|开放时间|实时)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class PlanIssue:
    code: str
    field: str
    message: str


class PlanValidationError(Exception):
    code = "PLAN_VALIDATION_FAILED"

    def __init__(self, issues: list[PlanIssue]) -> None:
        self.issues = issues
        super().__init__(self.code)


def validate_itinerary(
    itinerary: Itinerary,
    profile: TravelProfile,
    sources: Iterable[TrustedEvidence | ProviderResult[Any]],
) -> list[PlanIssue]:
    """Validate cross-model constraints without trusting model-authored metadata."""
    issues: list[PlanIssue] = []
    profile_start, profile_end = _profile_dates(profile)
    if profile_start is None or profile_end is None:
        issues.append(PlanIssue("PROFILE_DATES_INVALID", "profile", "Profile dates are required."))
    elif itinerary.start_date != profile_start or itinerary.end_date != profile_end:
        issues.append(PlanIssue("PROFILE_DATE_MISMATCH", "days", "Itinerary dates must match the profile."))

    if itinerary.budget.traveler_count != profile.travelers:
        issues.append(PlanIssue("TRAVELER_BASIS_MISMATCH", "budget.traveler_count", "Budget traveler count must match the profile."))
    if profile.budget_cny is not None and itinerary.budget.total > profile.budget_cny:
        issues.append(PlanIssue("BUDGET_EXCEEDED", "budget.total", "Budget must not exceed the confirmed CNY budget."))

    registry = _trusted_registry(sources)
    citations = _all_citations(itinerary)
    invalid_citations = [citation for citation in citations if not _citation_matches(citation, registry)]
    if invalid_citations:
        issues.append(PlanIssue("UNTRUSTED_EVIDENCE", "citations", "Citations must reference trusted provider evidence."))

    if _contains_variable_fact(itinerary) and not citations:
        issues.append(PlanIssue("UNSOURCED_FACT", "notes", "Variable facts require trusted evidence."))
    return issues


class Planner:
    """Parses one structured candidate and permits one bounded repair attempt."""

    def __init__(self, generate: Callable[[TravelProfile, object, list[str] | None], object]) -> None:
        self._generate = generate

    def plan(self, profile: TravelProfile, provider_results: object) -> Itinerary:
        repair_codes: list[str] | None = None
        for attempt in range(2):
            candidate = self._generate(profile, provider_results, repair_codes)
            itinerary, issues = self._validate_candidate(candidate, profile, provider_results)
            if itinerary is not None and not issues:
                return itinerary
            if attempt == 0:
                repair_codes = sorted({issue.code for issue in issues})
                continue
            raise PlanValidationError(issues)
        raise AssertionError("unreachable")

    @staticmethod
    def _validate_candidate(
        candidate: object, profile: TravelProfile, provider_results: object,
    ) -> tuple[Itinerary | None, list[PlanIssue]]:
        try:
            payload = json.loads(candidate) if isinstance(candidate, str) else candidate
            if not isinstance(payload, Mapping):
                raise ValueError("itinerary response must be a JSON object")
            itinerary = Itinerary.model_validate(payload)
        except (ValidationError, ValueError, TypeError, json.JSONDecodeError):
            return None, [PlanIssue("SCHEMA_INVALID", "itinerary", "The itinerary must match the public JSON schema.")]
        return itinerary, validate_itinerary(itinerary, profile, _iter_sources(provider_results))


def _profile_dates(profile: TravelProfile) -> tuple[date | None, date | None]:
    try:
        start = date.fromisoformat(profile.start_date or "")
        end = date.fromisoformat(profile.end_date or "")
    except ValueError:
        return None, None
    return start, end


def _iter_sources(value: object) -> Iterable[TrustedEvidence | ProviderResult[Any]]:
    if isinstance(value, (TrustedEvidence, ProviderResult)):
        return (value,)
    if isinstance(value, Iterable) and not isinstance(value, (str, bytes, Mapping)):
        return value
    return ()


def _trusted_registry(sources: Iterable[TrustedEvidence | ProviderResult[Any]]) -> dict[str, TrustedEvidence]:
    registry: dict[str, TrustedEvidence] = {}
    for source in sources:
        evidence_items = source.evidence if isinstance(source, ProviderResult) else (source,)
        for evidence in evidence_items:
            if (
                evidence.evidence_id
                and evidence.fact
                and evidence.source_type in _TRUSTED_SOURCE_TYPES
                and evidence.source_url.startswith("https://")
                and ".test" not in evidence.source_url.lower()
            ):
                registry[evidence.evidence_id] = evidence
    return registry


def _all_citations(itinerary: Itinerary) -> list[SourceCitation]:
    citations = list(itinerary.citations)
    for day in itinerary.days:
        for activity in (day.morning, day.afternoon, day.evening):
            citations.extend(activity.citations)
    return citations


def _citation_matches(citation: SourceCitation, registry: Mapping[str, TrustedEvidence]) -> bool:
    evidence = registry.get(citation.evidence_id)
    return bool(
        evidence
        and citation.source_url == evidence.source_url
        and citation.source_type == evidence.source_type
        and citation.fetched_at.tzinfo is not None
        and citation.freshness.strip()
    )


def _contains_variable_fact(itinerary: Itinerary) -> bool:
    text = [*itinerary.notes]
    for day in itinerary.days:
        for activity in (day.morning, day.afternoon, day.evening):
            text.extend((activity.title, *activity.notes))
    return any(_VARIABLE_FACT.search(item) for item in text)
