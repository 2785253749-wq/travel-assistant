from datetime import date
from dataclasses import dataclass, field
import re

from app.schemas import ProfileIssue, RawTravelProfile, TravelProfile


@dataclass(frozen=True)
class ExtractionCandidate:
    """Validated profile fields plus raw-field issues kept outside TravelProfile."""

    profile: TravelProfile
    issues: tuple[ProfileIssue, ...] = ()
    invalid_fields: dict[str, int] = field(default_factory=dict)


def build_extraction_candidate(
    current: TravelProfile, raw: RawTravelProfile,
) -> ExtractionCandidate:
    """Merge only values that satisfy the product traveler-count boundary."""
    fields = raw.model_dump()
    travelers = raw.travelers
    if travelers is None or 1 <= travelers <= 6:
        return ExtractionCandidate(merge_profile(current, TravelProfile.model_validate(fields)))

    fields["travelers"] = None
    issue = ProfileIssue(
        code="traveler_count",
        field="travelers",
        message="仅支持 1 至 6 人出行。",
    )
    return ExtractionCandidate(
        merge_profile(current, TravelProfile.model_validate(fields)),
        issues=(issue,),
        invalid_fields={"travelers": travelers},
    )


def merge_profile(current: TravelProfile, extracted: TravelProfile) -> TravelProfile:
    merged = current.model_dump()
    for field, value in extracted.model_dump().items():
        if value not in (None, "", []):
            merged[field] = value
    return TravelProfile.model_validate(merged)


def validate_profile(profile: TravelProfile) -> list[ProfileIssue]:
    issues: list[ProfileIssue] = []
    start_date = _parse_date(profile.start_date, "start_date", issues)
    end_date = _parse_date(profile.end_date, "end_date", issues)
    if start_date is not None and end_date is not None and end_date < start_date:
        issues.append(
            ProfileIssue(
                code="date_order",
                field="end_date",
                message="返程日期不能早于出发日期。",
            )
        )
    elif start_date is not None and end_date is not None:
        duration_days = (end_date - start_date).days + 1
        if not 2 <= duration_days <= 7:
            issues.append(
                ProfileIssue(
                    code="trip_duration",
                    field="end_date",
                    message="仅支持 2 至 7 天的国内自由行。",
                )
            )

    if profile.travelers is not None and not 1 <= profile.travelers <= 6:
        issues.append(
            ProfileIssue(
                code="traveler_count",
                field="travelers",
                message="仅支持 1 至 6 人出行。",
            )
        )
    return issues


def _parse_date(value: str | None, field: str, issues: list[ProfileIssue]) -> date | None:
    if value is None:
        return None
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        issues.append(
            ProfileIssue(
                code="invalid_date",
                field=field,
                message="日期必须使用 YYYY-MM-DD 格式。",
            )
        )
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        issues.append(
            ProfileIssue(
                code="invalid_date",
                field=field,
                message="日期必须使用 YYYY-MM-DD 格式。",
            )
        )
        return None
