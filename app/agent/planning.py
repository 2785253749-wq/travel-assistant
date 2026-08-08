"""Structured itinerary generation and deterministic safety validation."""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any

from pydantic import ValidationError

from app.agent.graph import TrustedEvidence
from app.providers.base import ProviderResult
from app.schemas import CHAT_REPLY_MAX_LENGTH, Itinerary, SourceCitation, TravelProfile


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

    if not _has_safe_display_text(itinerary, profile):
        issues.append(PlanIssue(
            "UNSOURCED_DISPLAY_FACT",
            "itinerary",
            "Display text may contain recommendations, not unverified variable facts.",
        ))

    if itinerary.budget.traveler_count != profile.travelers:
        issues.append(PlanIssue("TRAVELER_BASIS_MISMATCH", "budget.traveler_count", "Budget traveler count must match the profile."))
    if profile.budget_cny is not None and itinerary.budget.trip_total > profile.budget_cny:
        issues.append(PlanIssue("BUDGET_EXCEEDED", "budget.total", "Budget must not exceed the confirmed CNY budget."))

    registry, source_issues = _trusted_registry(sources, now or _utc_now)
    issues.extend(source_issues)
    issues.extend(_direct_claim_issues(itinerary, registry))
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
            itinerary = Itinerary.model_validate(_canonicalize_display_payload(payload, profile))
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


def _direct_claim_issues(itinerary: Itinerary, registry: Mapping[str, TrustedEvidence]) -> list[PlanIssue]:
    claim_mismatch = False
    citation_mismatch = False
    for day in itinerary.days:
        for activity in (day.morning, day.afternoon, day.evening):
            for claim in activity.facts:
                evidence = registry.get(claim.evidence_id)
                if evidence is None or claim.text != evidence.fact:
                    claim_mismatch = True
                    continue
                if not any(
                    citation.evidence_id == claim.evidence_id and _citation_matches(citation, registry)
                    for citation in activity.citations
                ):
                    citation_mismatch = True

    issues: list[PlanIssue] = []
    if claim_mismatch:
        issues.append(PlanIssue(
            "CLAIM_EVIDENCE_MISMATCH",
            "facts",
            "Each activity fact must exactly match current trusted evidence.",
        ))
    if citation_mismatch:
        issues.append(PlanIssue(
            "UNTRUSTED_EVIDENCE",
            "citations",
            "Each activity fact requires its canonical citation.",
        ))
    return issues


def _canonical_itinerary_title(profile: TravelProfile) -> str | None:
    profile_start, profile_end = _profile_dates(profile)
    if profile_start is None or profile_end is None or profile_end < profile_start:
        return None
    destination = (profile.destination or "Destination").strip() or "Destination"
    day_count = (profile_end - profile_start).days + 1
    return f"{destination} | {day_count}-day itinerary"


def _canonical_activity_title(day_number: int, slot: str) -> str:
    return f"Day {day_number} {slot}"


_DEFINITIVE_VARIABLE_FACT = re.compile(
    r"(?:\b(?:available|availability|sold\s+out|opens?|closes?|inventory)\b|"
    r"(?:可订|有房|售罄|库存|余票|营业|开放时间|闭馆))",
    re.IGNORECASE,
)
_NUMERIC_PRICE_FACT = re.compile(
    r"(?:\b(?:price|cost|fare)\b|(?:价格|票价|费用)).{0,40}(?:\d|cny|rmb|人民币|元)",
    re.IGNORECASE,
)


def _safe_display_text(value: object, *, max_length: int) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    if not normalized or len(normalized) > max_length:
        return None
    if _DEFINITIVE_VARIABLE_FACT.search(normalized) or _NUMERIC_PRICE_FACT.search(normalized):
        return None
    return normalized


def _safe_display_notes(value: object) -> list[str]:
    if not isinstance(value, list) or len(value) > 40:
        return []
    notes: list[str] = []
    for raw_note in value:
        note = _safe_display_text(raw_note, max_length=500)
        if note is None:
            return []
        notes.append(note)
    return notes


def _canonicalize_display_payload(payload: Mapping[object, object], profile: TravelProfile) -> dict[object, object]:
    """Keep readable recommendations while replacing unsafe or malformed display fields."""
    title = _canonical_itinerary_title(profile)
    raw_days = payload.get("days")
    if title is None or not isinstance(raw_days, list):
        raise ValueError("itinerary days must be a JSON array")

    canonical = dict(payload)
    canonical["title"] = _safe_display_text(payload.get("title"), max_length=300) or title
    canonical["notes"] = _safe_display_notes(payload.get("notes", []))
    # Booking links are server-owned and are attached only after validation.
    canonical["booking_links"] = None
    days: list[dict[object, object]] = []
    for day_number, raw_day in enumerate(raw_days, start=1):
        if not isinstance(raw_day, Mapping):
            raise ValueError("each itinerary day must be a JSON object")
        day = dict(raw_day)
        for slot in _ACTIVITY_SLOTS:
            raw_activity = raw_day.get(slot)
            if not isinstance(raw_activity, Mapping):
                raise ValueError("each itinerary activity must be a JSON object")
            activity = dict(raw_activity)
            activity["title"] = (
                _safe_display_text(raw_activity.get("title"), max_length=300)
                or _canonical_activity_title(day_number, slot)
            )
            activity["notes"] = _safe_display_notes(raw_activity.get("notes", []))
            day[slot] = activity
        days.append(day)
    canonical["days"] = days
    return canonical


def _normalize_display_text(itinerary: Itinerary, profile: TravelProfile) -> None:
    title = _canonical_itinerary_title(profile)
    itinerary.title = _safe_display_text(itinerary.title, max_length=300) or title or "Trip itinerary"
    itinerary.notes = _safe_display_notes(itinerary.notes)
    itinerary.booking_links = None
    for day_number, day in enumerate(itinerary.days, start=1):
        for slot in _ACTIVITY_SLOTS:
            activity = getattr(day, slot)
            activity.title = (
                _safe_display_text(activity.title, max_length=300)
                or _canonical_activity_title(day_number, slot)
            )
            activity.notes = _safe_display_notes(activity.notes)


def _has_safe_display_text(itinerary: Itinerary, profile: TravelProfile) -> bool:
    if _canonical_itinerary_title(profile) is None:
        return False
    try:
        values = [itinerary.title, *itinerary.notes]
        for day in itinerary.days:
            for activity in (day.morning, day.afternoon, day.evening):
                values.extend((activity.title, *activity.notes))
        return all(
            _safe_display_text(value, max_length=500) is not None
            for value in values
        )
    except (AttributeError, TypeError):
        return False


def _all_claims(itinerary: Itinerary) -> list[tuple[object, object]]:
    claims: list[tuple[object, object]] = []
    for day in itinerary.days:
        for activity in (day.morning, day.afternoon, day.evening):
            claims.extend((activity, claim) for claim in activity.facts)
    return claims


def _normalize_claims(itinerary: Itinerary, registry: Mapping[str, TrustedEvidence]) -> tuple[Itinerary, list[PlanIssue]]:
    for activity, claim in _all_claims(itinerary):
        evidence = registry.get(claim.evidence_id)
        if evidence is None or claim.text != evidence.fact:
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


def render_itinerary_markdown(
    itinerary: Itinerary,
    *,
    max_length: int = CHAT_REPLY_MAX_LENGTH,
) -> str:
    """Create a readable, storage-safe summary while the full plan stays structured."""

    def clipped(value: object, limit: int) -> str:
        text = " ".join(str(value).split())
        return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"

    lines = [
        f"# {clipped(itinerary.title, 120)}",
        "",
        f"日期：{itinerary.start_date.isoformat()} 至 {itinerary.end_date.isoformat()}",
    ]
    slot_labels = {"morning": "上午", "afternoon": "下午", "evening": "晚上"}
    for day in itinerary.days:
        lines.extend(("", f"## {day.date.isoformat()}"))
        for slot in _ACTIVITY_SLOTS:
            activity = getattr(day, slot)
            lines.append(
                f"- {slot_labels[slot]} {activity.start_time}–{activity.end_time}："
                f"{clipped(activity.title, 90)}"
            )

    budget = itinerary.budget
    lines.extend(
        (
            "",
            "## 预算估算",
            f"- 行程合计：{budget.trip_total} {budget.currency}（{budget.traveler_count} 人）",
            "- 该预算为规划估算，不是实时价格或库存。",
        )
    )
    if itinerary.assumptions:
        lines.extend(("", "## 规划假设"))
        for assumption in itinerary.assumptions[:4]:
            lines.append(f"- {clipped(assumption.description, 120)}")
    if itinerary.notes:
        lines.extend(("", "## 提醒"))
        for note in itinerary.notes[:3]:
            lines.append(f"- {clipped(note, 120)}")
    if itinerary.booking_links is not None:
        links = itinerary.booking_links
        lines.extend(
            (
                "",
                "## 第三方搜索入口",
                f"- 火车：{links.train}",
                f"- 酒店：{links.hotel}",
                f"- 航班：{links.flight}",
                f"- {clipped(links.disclaimer, 200)}",
            )
        )

    summary = "\n".join(lines).strip()
    truncation = "\n\n…完整活动说明、事实来源与待确认项请查看结构化行程卡。"
    if len(summary) > max_length:
        summary = summary[: max_length - len(truncation)].rstrip() + truncation
    return summary
