"""Structured itinerary generation and deterministic safety validation."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any

from pydantic import ValidationError

from app.agent.graph import TrustedEvidence
from app.providers.base import ProviderResult
from app.schemas import Itinerary, SourceCitation, TravelProfile


_TRUSTED_SOURCE_TYPES = {"official", "government", "trusted_provider"}
_ACTIVITY_SLOTS = ("morning", "afternoon", "evening")


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


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def validate_itinerary(
    itinerary: Itinerary,
    profile: TravelProfile,
    sources: Iterable[TrustedEvidence | ProviderResult[Any]], now: Callable[[], datetime] | None = None,
) -> list[PlanIssue]:
    """Validate cross-model constraints without trusting model-authored metadata."""
    issues: list[PlanIssue] = []
    profile_start, profile_end = _profile_dates(profile)
    if profile_start is None or profile_end is None:
        issues.append(PlanIssue("PROFILE_DATES_INVALID", "profile", "Profile dates are required."))
    elif itinerary.start_date != profile_start or itinerary.end_date != profile_end:
        issues.append(PlanIssue("PROFILE_DATE_MISMATCH", "days", "Itinerary dates must match the profile."))

    if not _has_canonical_display_text(itinerary, profile):
        issues.append(PlanIssue(
            "NON_CANONICAL_DISPLAY_TEXT",
            "itinerary",
            "Display text must match server-generated templates.",
        ))

    if itinerary.budget.traveler_count != profile.travelers:
        issues.append(PlanIssue("TRAVELER_BASIS_MISMATCH", "budget.traveler_count", "Budget traveler count must match the profile."))
    if profile.budget_cny is not None and itinerary.budget.trip_total > profile.budget_cny:
        issues.append(PlanIssue("BUDGET_EXCEEDED", "budget.total", "Budget must not exceed the confirmed CNY budget."))

    registry, source_issues = _trusted_registry(sources, now or _utc_now)
    issues.extend(source_issues)
    citations = _all_citations(itinerary)
    invalid_citations = [citation for citation in citations if not _citation_matches(citation, registry)]
    if invalid_citations:
        issues.append(PlanIssue("UNTRUSTED_EVIDENCE", "citations", "Citations must reference trusted provider evidence."))

    return issues


class Planner:
    """Parses one structured candidate and permits one bounded repair attempt."""

    def __init__(self, generate: Callable[[TravelProfile, object, list[str] | None], object], now: Callable[[], datetime] = _utc_now) -> None:
        self._generate = generate
        self._now = now

    def plan(self, profile: TravelProfile, provider_results: object) -> Itinerary:
        repair_codes: list[str] | None = None
        for attempt in range(2):
            candidate = self._generate(profile, provider_results, repair_codes)
            itinerary, issues = self._validate_candidate(candidate, profile, provider_results, self._now)
            if itinerary is not None and not issues:
                return itinerary
            if attempt == 0:
                repair_codes = sorted({issue.code for issue in issues})
                continue
            raise PlanValidationError(issues)
        raise AssertionError("unreachable")

    @staticmethod
    def _validate_candidate(
        candidate: object, profile: TravelProfile, provider_results: object, now: Callable[[], datetime],
    ) -> tuple[Itinerary | None, list[PlanIssue]]:
        try:
            payload = json.loads(candidate) if isinstance(candidate, str) else candidate
            if not isinstance(payload, Mapping):
                raise ValueError("itinerary response must be a JSON object")
            itinerary = Itinerary.model_validate(payload)
        except (ValidationError, ValueError, TypeError, json.JSONDecodeError):
            return None, [PlanIssue("SCHEMA_INVALID", "itinerary", "The itinerary must match the public JSON schema.")]
        registry, source_issues = _trusted_registry(_iter_sources(provider_results), now)
        if source_issues:
            return None, source_issues
        _normalize_display_text(itinerary, profile)
        normalized, claim_issues = _normalize_claims(itinerary, registry)
        if claim_issues:
            return None, claim_issues
        return normalized, validate_itinerary(normalized, profile, _iter_sources(provider_results), now)


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


def _trusted_registry(sources: Iterable[TrustedEvidence | ProviderResult[Any]], now: Callable[[], datetime]) -> tuple[dict[str, TrustedEvidence], list[PlanIssue]]:
    registry: dict[str, TrustedEvidence] = {}
    issues: list[PlanIssue] = []
    for source in sources:
        evidence_items = source.evidence if isinstance(source, ProviderResult) else (source,)
        for evidence in evidence_items:
            fetched_at = source.fetched_at if isinstance(source, ProviderResult) else getattr(evidence, "fetched_at", None)
            if (
                evidence.evidence_id
                and evidence.fact
                and evidence.source_type in _TRUSTED_SOURCE_TYPES
                and evidence.source_url.startswith("https://")
                and ".test" not in evidence.source_url.lower()
            ) and fetched_at is not None and fetched_at.tzinfo is not None:
                if fetched_at > now() or now() - fetched_at > _ttl(evidence.source_type):
                    issues.append(PlanIssue("STALE_EVIDENCE", "sources", "Evidence freshness is outside its allowed TTL."))
                else:
                    registry[evidence.evidence_id] = TrustedEvidence(evidence.evidence_id, evidence.fact, evidence.source_url, evidence.source_type, fetched_at)
            elif evidence.evidence_id:
                issues.append(PlanIssue("MISSING_EVIDENCE_TIMESTAMP", "sources", "Trusted evidence requires provider fetch time."))
    return registry, issues


def _ttl(source_type: str) -> timedelta:
    return timedelta(hours=24 if source_type == "trusted_provider" else 24 * 7)


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
        and citation.fact == evidence.fact
        and citation.fetched_at == evidence.fetched_at
        and citation.freshness == f"Fetched {evidence.fetched_at.isoformat()}; reference only."
    )


def _canonical_itinerary_title(profile: TravelProfile) -> str | None:
    profile_start, profile_end = _profile_dates(profile)
    if profile_start is None or profile_end is None or profile_end < profile_start:
        return None
    destination = (profile.destination or "Destination").strip() or "Destination"
    day_count = (profile_end - profile_start).days + 1
    return f"{destination} | {day_count}-day itinerary"


def _canonical_activity_title(day_number: int, slot: str) -> str:
    return f"Day {day_number} {slot}"


def _normalize_display_text(itinerary: Itinerary, profile: TravelProfile) -> None:
    title = _canonical_itinerary_title(profile)
    if title is not None:
        itinerary.title = title
    itinerary.notes = []
    for day_number, day in enumerate(itinerary.days, start=1):
        for slot in _ACTIVITY_SLOTS:
            activity = getattr(day, slot)
            activity.title = _canonical_activity_title(day_number, slot)
            activity.notes = []


def _has_canonical_display_text(itinerary: Itinerary, profile: TravelProfile) -> bool:
    expected_title = _canonical_itinerary_title(profile)
    if expected_title is None or itinerary.title != expected_title or itinerary.notes != []:
        return False
    try:
        return all(
            activity.title == _canonical_activity_title(day_number, slot) and activity.notes == []
            for day_number, day in enumerate(itinerary.days, start=1)
            for slot in _ACTIVITY_SLOTS
            for activity in (getattr(day, slot),)
        )
    except (AttributeError, TypeError):
        return False


def _all_claims(itinerary: Itinerary) -> list[tuple[object, object]]:
    claims: list[tuple[object, object]] = []
    for day in itinerary.days:
        for activity in (day.morning, day.afternoon, day.evening):
            claims.extend((activity, claim) for claim in activity.facts)
    return claims


def _normalize(text: str) -> str:
    return " ".join(text.casefold().split())


def _normalize_claims(itinerary: Itinerary, registry: Mapping[str, TrustedEvidence]) -> tuple[Itinerary, list[PlanIssue]]:
    for activity, claim in _all_claims(itinerary):
        evidence = registry.get(claim.evidence_id)
        if evidence is None or _normalize(claim.text) != _normalize(evidence.fact):
            return itinerary, [PlanIssue("CLAIM_EVIDENCE_MISMATCH", "claims", "Each claim must exactly match its trusted evidence.")]
    # User/model supplied citation metadata never survives normalization.
    for day in itinerary.days:
        for activity in (day.morning, day.afternoon, day.evening):
            activity.citations = []
    for activity, claim in _all_claims(itinerary):
        evidence = registry[claim.evidence_id]
        if any(citation.evidence_id == evidence.evidence_id and citation.fact == evidence.fact for citation in activity.citations):
            continue
        activity.citations.append(SourceCitation(
            evidence_id=evidence.evidence_id, source_url=evidence.source_url, source_type=evidence.source_type,
            fetched_at=evidence.fetched_at, freshness=f"Fetched {evidence.fetched_at.isoformat()}; reference only.", fact=evidence.fact,
        ))
    itinerary.citations = []
    return itinerary, []
