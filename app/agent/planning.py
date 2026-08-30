"""Structured itinerary generation and deterministic safety validation."""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from time import monotonic
from typing import Any

from pydantic import ValidationError

from app.agent.graph import TrustedEvidence
from app.core.logging import operational_context
from app.providers.base import ProviderResult
from app.schemas import CHAT_REPLY_MAX_LENGTH, Itinerary, SourceCitation, TravelProfile
from app.trips.transport import TripTransportContext, selected_seat_price


_TRUSTED_SOURCE_TYPES = {"official", "government", "trusted_provider"}
_ACTIVITY_SLOTS = ("morning", "afternoon", "evening")
_TRANSPORT_BUFFER_MINUTES = 90
_DAY_LAST_MINUTE = 23 * 60 + 59
_ACTIVITY_GAP_MINUTES = 30
_CHINA_TIMEZONE = timezone(timedelta(hours=8))


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
            repair_started = monotonic() if repair_codes is not None else None
            if repair_started is not None:
                logging.getLogger("app.planner").info(
                    "planner repair start",
                    extra=operational_context(attempt=attempt + 1),
                )
            try:
                candidate = self._generate(profile, provider_results, repair_codes)
            except Exception:
                if repair_started is not None:
                    logging.getLogger("app.planner").warning(
                        "planner repair end",
                        extra=operational_context(
                            attempt=attempt + 1,
                            stage="failure",
                            elapsed_seconds=round(monotonic() - repair_started, 3),
                        ),
                    )
                raise
            if repair_started is not None:
                logging.getLogger("app.planner").info(
                    "planner repair end",
                    extra=operational_context(
                        attempt=attempt + 1,
                        stage="success",
                        elapsed_seconds=round(monotonic() - repair_started, 3),
                    ),
                )
            validation_started = monotonic()
            logging.getLogger("app.planner").info(
                "planner validation start",
                extra=operational_context(attempt=attempt + 1),
            )
            itinerary, issues = self._validate_candidate(candidate, profile, provider_results, self._now)
            logging.getLogger("app.planner").info(
                "planner validation end",
                extra=operational_context(
                    attempt=attempt + 1,
                    stage="success" if itinerary is not None and not issues else "failure",
                    validation_codes=",".join(sorted({issue.code for issue in issues})) or None,
                    elapsed_seconds=round(monotonic() - validation_started, 3),
                ),
            )
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
        normalized = _apply_transport_context(normalized, provider_results, profile)
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
    results = getattr(value, "results", None)
    if results is not None:
        return _iter_sources(results)
    if isinstance(value, Iterable) and not isinstance(value, (str, bytes, Mapping)):
        return value
    return ()


def _transport_context(value: object) -> TripTransportContext | None:
    context = getattr(value, "transport_context", None)
    return context if isinstance(context, TripTransportContext) else context


def _apply_transport_context(
    itinerary: Itinerary,
    provider_results: object,
    profile: TravelProfile,
) -> Itinerary:
    context = _transport_context(provider_results)
    if context is None:
        return itinerary
    payload = itinerary.model_dump(mode="json")
    days = payload["days"]
    if context.outbound is not None:
        arrival = context.outbound.arrival_at.astimezone(_CHINA_TIMEZONE)
        day = _day_for_date(days, arrival.date())
        if day is not None:
            _constrain_day(day, start_minute=_minutes(arrival.time()) + _TRANSPORT_BUFFER_MINUTES)
    if context.return_option is not None:
        departure = context.return_option.departure_at.astimezone(_CHINA_TIMEZONE)
        day = _day_for_date(days, departure.date())
        if day is not None:
            _constrain_day(day, end_minute=_minutes(departure.time()) - _TRANSPORT_BUFFER_MINUTES)
    _remove_misplaced_train_activity_titles(days, context)
    _override_transport_budget(payload, context, profile)
    return Itinerary.model_validate(payload)


def _day_for_date(days: list[dict[str, Any]], target: date) -> dict[str, Any] | None:
    target_text = target.isoformat()
    return next((day for day in days if day.get("date") == target_text), None)


def _minutes(value: time) -> int:
    return value.hour * 60 + value.minute


def _constrain_day(
    day: dict[str, Any],
    *,
    start_minute: int | None = None,
    end_minute: int | None = None,
) -> None:
    activities = [day[slot] for slot in _ACTIVITY_SLOTS]
    original = [
        (_minutes(time.fromisoformat(activity["start_time"])), _minutes(time.fromisoformat(activity["end_time"])))
        for activity in activities
    ]
    if (
        (start_minute is None or all(start >= start_minute for start, _ in original))
        and (end_minute is None or all(end <= end_minute for _, end in original))
    ):
        return

    lower = start_minute if start_minute is not None else 0
    upper = end_minute if end_minute is not None else _DAY_LAST_MINUTE
    if upper <= lower:
        return
    durations = [end - start for start, end in original]
    available = upper - lower
    required = sum(durations) + _ACTIVITY_GAP_MINUTES * (len(durations) - 1)
    if required > available:
        compact_duration = max(
            1,
            (available - _ACTIVITY_GAP_MINUTES * (len(durations) - 1)) // len(durations),
        )
        durations = [min(duration, compact_duration) for duration in durations]

    if start_minute is not None:
        cursor = lower
        schedule: list[tuple[int, int]] = []
        for duration in durations:
            finish = min(cursor + duration, upper)
            schedule.append((cursor, finish))
            cursor = finish + _ACTIVITY_GAP_MINUTES
    else:
        cursor = upper
        schedule = [(0, 0)] * len(durations)
        for index in range(len(durations) - 1, -1, -1):
            begin = max(lower, cursor - durations[index])
            schedule[index] = (begin, cursor)
            cursor = begin - _ACTIVITY_GAP_MINUTES

    for activity, (start, finish) in zip(activities, schedule, strict=True):
        activity["start_time"] = _format_minutes(start)
        activity["end_time"] = _format_minutes(finish)


def _format_minutes(value: int) -> str:
    value = max(0, min(_DAY_LAST_MINUTE, value))
    return f"{value // 60:02d}:{value % 60:02d}"


def _remove_misplaced_train_activity_titles(
    days: list[dict[str, Any]], context: TripTransportContext,
) -> None:
    train_numbers = {
        option.train_no
        for option in (context.outbound, context.return_option)
        if option is not None
    }
    if not train_numbers:
        return
    for day in days:
        for slot in _ACTIVITY_SLOTS:
            activity = day[slot]
            if not _is_local_activity_window(day, activity, context):
                continue
            title = str(activity.get("title", ""))
            if _describes_train(title, train_numbers):
                activity["title"] = "抵达后当地活动"
            activity["notes"] = [
                note for note in activity.get("notes", [])
                if not _describes_train(str(note), train_numbers)
            ]


def _is_local_activity_window(
    day: dict[str, Any], activity: dict[str, Any], context: TripTransportContext,
) -> bool:
    try:
        activity_date = date.fromisoformat(str(day["date"]))
        start = _minutes(time.fromisoformat(str(activity["start_time"])))
        end = _minutes(time.fromisoformat(str(activity["end_time"])))
    except (KeyError, TypeError, ValueError):
        return False
    if context.outbound is not None:
        arrival = context.outbound.arrival_at.astimezone(_CHINA_TIMEZONE)
        if activity_date == arrival.date() and start >= _minutes(arrival.time()) + _TRANSPORT_BUFFER_MINUTES:
            return True
    if context.return_option is not None:
        departure = context.return_option.departure_at.astimezone(_CHINA_TIMEZONE)
        if activity_date == departure.date() and end <= _minutes(departure.time()) - _TRANSPORT_BUFFER_MINUTES:
            return True
    return False


def _describes_train(text: str, train_numbers: set[str]) -> bool:
    return any(train_no in text for train_no in train_numbers) or (
        "乘坐" in text and any(term in text for term in ("列车", "高铁", "动车", "火车"))
    ) or "站出发" in text or ("全程" in text and "站" in text)


def _override_transport_budget(
    payload: dict[str, Any],
    context: TripTransportContext,
    profile: TravelProfile,
) -> None:
    outbound_requested = getattr(context, "outbound_requested", context.outbound is not None)
    return_requested = getattr(context, "return_requested", context.return_option is not None)
    selected = []
    if outbound_requested:
        selected.append(context.outbound)
    if return_requested:
        selected.append(context.return_option)
    seat_type = getattr(context, "seat_type", "二等座")
    if not selected:
        return
    traveler_count = profile.travelers or payload["budget"]["traveler_count"]
    budget = payload["budget"]
    prices = [selected_seat_price(option, seat_type) for option in selected]
    known_prices = [price for price in prices if price is not None]
    if not known_prices:
        return
    basis_multiplier = traveler_count if budget["traveler_basis"] == "trip_total" else 1
    known_transport = sum(known_prices) * basis_multiplier
    missing_count = len(prices) - len(known_prices)
    if missing_count:
        original_estimate = Decimal(str(budget["transport"]))
        estimate_per_leg = original_estimate / len(prices)
        known_per_leg = known_transport / len(known_prices)
        missing_transport = max(estimate_per_leg, known_per_leg) * missing_count
        transport_amount = known_transport + missing_transport
    else:
        transport_amount = known_transport
    trip_total = transport_amount if budget["traveler_basis"] == "trip_total" else transport_amount * traveler_count
    budget["transport"] = int(transport_amount)
    budget["total"] = sum(budget[name] for name in ("transport", "hotel", "food", "tickets", "reserve", "other"))
    budget["trip_total"] = budget["total"] if budget["traveler_basis"] == "trip_total" else budget["total"] * budget["traveler_count"]
    estimate = budget["estimate"]
    estimate["low"] = min(estimate["low"], budget["total"])
    estimate["point"] = budget["total"]
    estimate["high"] = max(estimate["high"], budget["total"])


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

    def period_label(activity: Any, slot: str) -> str:
        try:
            start = time.fromisoformat(activity.start_time)
        except (TypeError, ValueError):
            return slot_labels[slot]
        minute = _minutes(start)
        if minute >= 18 * 60:
            return "晚上"
        if minute >= 12 * 60:
            return "下午"
        return "上午"

    for day in itinerary.days:
        lines.extend(("", f"## {day.date.isoformat()}"))
        for slot in _ACTIVITY_SLOTS:
            activity = getattr(day, slot)
            lines.append(
                f"- {period_label(activity, slot)} {activity.start_time}–{activity.end_time}："
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
