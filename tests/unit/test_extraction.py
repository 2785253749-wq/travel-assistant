import pytest
from pydantic import ValidationError

from app.agent.extraction import merge_profile, validate_profile
from app.schemas import TravelProfile


def test_empty_extraction_does_not_erase_confirmed_values():
    current = TravelProfile(origin="上海", travelers=2)
    extracted = TravelProfile(origin=None, travelers=None)

    assert merge_profile(current, extracted) == current


def test_merge_profile_applies_explicit_extracted_values():
    current = TravelProfile(origin="上海", travelers=2, preferences=["美食"])
    extracted = TravelProfile(destination="杭州", travelers=3, preferences=["自然"])

    merged = merge_profile(current, extracted)

    assert merged == TravelProfile(
        origin="上海", destination="杭州", travelers=3, preferences=["自然"]
    )


def test_profile_validation_returns_stable_codes_for_product_boundaries():
    profile = TravelProfile(
        start_date="2026-10-05",
        end_date="2026-10-01",
        travelers=7,
    )

    issues = validate_profile(profile)

    assert [(issue.code, issue.field) for issue in issues] == [
        ("date_order", "end_date"),
        ("traveler_count", "travelers"),
    ]


def test_profile_validation_rejects_trips_longer_than_seven_days():
    profile = TravelProfile(start_date="2026-10-01", end_date="2026-10-08")

    issues = validate_profile(profile)

    assert [(issue.code, issue.field) for issue in issues] == [
        ("trip_duration", "end_date"),
    ]


@pytest.mark.parametrize("travelers", [0, -1])
def test_travel_profile_rejects_nonpositive_traveler_counts(travelers):
    with pytest.raises(ValidationError):
        TravelProfile(travelers=travelers)


@pytest.mark.parametrize("travelers", [0, -1])
def test_profile_validation_keeps_stable_code_for_bypassed_nonpositive_counts(travelers):
    profile = TravelProfile.model_construct(travelers=travelers)

    issues = validate_profile(profile)

    assert [(issue.code, issue.field) for issue in issues] == [
        ("traveler_count", "travelers"),
    ]


def test_profile_validation_rejects_compact_date_format():
    profile = TravelProfile(start_date="20261001")

    issues = validate_profile(profile)

    assert [(issue.code, issue.field) for issue in issues] == [
        ("invalid_date", "start_date"),
    ]
