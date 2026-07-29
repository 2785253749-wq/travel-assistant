from datetime import date, datetime, timezone

import pytest
from pydantic import ValidationError

from app.agent.graph import TrustedEvidence
from app.agent.planning import PlanValidationError, Planner, validate_itinerary
from app.schemas import Activity, BudgetBreakdown, Itinerary, ItineraryDay, SourceCitation, TravelProfile


def profile_factory(**overrides: object) -> TravelProfile:
    values = {
        "origin": "Shanghai",
        "destination": "Hangzhou",
        "start_date": "2026-08-01",
        "end_date": "2026-08-02",
        "travelers": 2,
        "budget_cny": 3500,
    }
    values.update(overrides)
    return TravelProfile(**values)


def itinerary_factory(**overrides: object) -> Itinerary:
    day_one = ItineraryDay(
        date=date(2026, 8, 1),
        morning=Activity(title="Walk", start_time="09:00", end_time="11:00"),
        afternoon=Activity(title="Museum", start_time="13:00", end_time="16:00"),
        evening=Activity(title="Dinner", start_time="18:00", end_time="20:00"),
    )
    day_two = ItineraryDay(
        date=date(2026, 8, 2),
        morning=Activity(title="Park", start_time="09:00", end_time="11:00"),
        afternoon=Activity(title="Market", start_time="13:00", end_time="15:00"),
        evening=Activity(title="Return", start_time="17:00", end_time="19:00"),
    )
    values = {
        "title": "Hangzhou weekend",
        "start_date": date(2026, 8, 1),
        "end_date": date(2026, 8, 2),
        "days": [day_one, day_two],
        "budget": {
            "transport": 1000,
            "hotel": 1200,
            "food": 600,
            "tickets": 200,
            "reserve": 500,
            "other": 0,
            "total": 3500,
            "currency": "CNY",
            "traveler_basis": "trip_total",
            "traveler_count": 2,
        },
        "notes": [],
        "assumptions": ["All costs are planning estimates, not live prices."],
    }
    values.update(overrides)
    return Itinerary(**values)


def test_budget_total_matches_profile() -> None:
    itinerary = itinerary_factory()

    assert validate_itinerary(itinerary, profile_factory(), []) == []


def test_budget_schema_rejects_total_that_does_not_match_categories() -> None:
    with pytest.raises(ValidationError):
        BudgetBreakdown(
            transport=1000, hotel=1200, food=600, tickets=200, reserve=500,
            other=0, total=3499, currency="CNY", traveler_basis="trip_total", traveler_count=2,
        )


def test_itinerary_rejects_noncontinuous_dates_and_overlapping_activities() -> None:
    with pytest.raises(ValidationError):
        itinerary_factory(days=[
            ItineraryDay(
                date=date(2026, 8, 1),
                morning=Activity(title="Morning", start_time="10:00", end_time="12:00"),
                afternoon=Activity(title="Overlap", start_time="11:00", end_time="14:00"),
                evening=Activity(title="Evening", start_time="18:00", end_time="20:00"),
            ),
            ItineraryDay(
                date=date(2026, 8, 3),
                morning=Activity(title="Morning", start_time="09:00", end_time="11:00"),
                afternoon=Activity(title="Afternoon", start_time="13:00", end_time="15:00"),
                evening=Activity(title="Evening", start_time="18:00", end_time="20:00"),
            ),
        ])


def test_unverified_price_is_rejected() -> None:
    itinerary = itinerary_factory(notes=["Hotel live price is CNY 399 per night."])

    issues = validate_itinerary(itinerary, profile_factory(), sources=[])

    assert {issue.code for issue in issues} == {"UNSOURCED_FACT"}


def test_citations_must_reference_trusted_evidence_and_disclose_freshness() -> None:
    evidence = TrustedEvidence(
        evidence_id="place-1",
        fact="West Lake is in Hangzhou.",
        source_url="https://photon.komoot.io/api/",
        source_type="trusted_provider",
    )
    citation = SourceCitation(
        evidence_id="place-1",
        source_url="https://photon.komoot.io/api/",
        source_type="trusted_provider",
        fetched_at=datetime(2026, 7, 1, tzinfo=timezone.utc),
        freshness="reference only; verify before departure",
    )
    itinerary = itinerary_factory(days=[
        ItineraryDay(
            date=date(2026, 8, 1),
            morning=Activity(title="West Lake", start_time="09:00", end_time="11:00", citations=[citation]),
            afternoon=Activity(title="Museum", start_time="13:00", end_time="16:00"),
            evening=Activity(title="Dinner", start_time="18:00", end_time="20:00"),
        ),
        itinerary_factory().days[1],
    ])

    assert validate_itinerary(itinerary, profile_factory(), [evidence]) == []

    invalid = itinerary.model_copy(update={"citations": [citation.model_copy(update={"evidence_id": "made-up"})]})
    assert {issue.code for issue in validate_itinerary(invalid, profile_factory(), [evidence])} == {"UNTRUSTED_EVIDENCE"}


def test_planner_repairs_once_then_fails_closed() -> None:
    invalid = itinerary_factory().model_dump(mode="json")
    invalid["budget"]["total"] = 1
    calls: list[list[str] | None] = []

    def generate(_profile: TravelProfile, _providers: object, repair_codes: list[str] | None) -> dict:
        calls.append(repair_codes)
        return invalid

    with pytest.raises(PlanValidationError, match="PLAN_VALIDATION_FAILED"):
        Planner(generate).plan(profile_factory(), [])

    assert calls[0] is None
    assert calls[1] == ["SCHEMA_INVALID"]
