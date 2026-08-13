from datetime import date, datetime, timezone
from unittest.mock import Mock

import pytest
from pydantic import ValidationError

from app.agent.graph import SafeTravelAgent, TrustedEvidence, enrich_itinerary
from app.agent.intent import IntentResult
from app.rag.models import RetrievedChunk
from app.rag.service import RagAnswer
from app.schemas import ItineraryWeather
from app.agent.planning import PlanValidationError, Planner, validate_itinerary
from app.providers.base import ProviderResult
from app.schemas import Activity, BudgetBreakdown, EstimateRange, FactClaim, Itinerary, ItineraryDay, PlanningAssumption, SourceCitation, TravelProfile


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
        morning=Activity(title="Day 1 morning", start_time="09:00", end_time="11:00"),
        afternoon=Activity(title="Day 1 afternoon", start_time="13:00", end_time="16:00"),
        evening=Activity(title="Day 1 evening", start_time="18:00", end_time="20:00"),
    )
    day_two = ItineraryDay(
        date=date(2026, 8, 2),
        morning=Activity(title="Day 2 morning", start_time="09:00", end_time="11:00"),
        afternoon=Activity(title="Day 2 afternoon", start_time="13:00", end_time="15:00"),
        evening=Activity(title="Day 2 evening", start_time="17:00", end_time="19:00"),
    )
    values = {
        "title": "Hangzhou | 2-day itinerary",
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
            "trip_total": 3500,
            "estimate": {"low": 3200, "point": 3500, "high": 3800, "currency": "CNY", "basis": "trip_total", "assumption_id": "cost-v1"},
        },
        "notes": [],
        "assumptions": [{"assumption_id": "cost-v1", "category": "budget", "description": "Offline planning estimate; verify before departure."}],
    }
    values.update(overrides)
    return Itinerary(**values)


def test_budget_total_matches_profile() -> None:
    itinerary = itinerary_factory()

    assert validate_itinerary(itinerary, profile_factory(), []) == []


def test_knowledge_question_returns_chinese_source_labels_without_planner_call() -> None:
    class Classifier:
        def classify(self, *_args):
            return IntentResult(intent="travel_knowledge", confidence=1.0)

    class Knowledge:
        def answer(self, *_args, **_kwargs):
            return RagAnswer.grounded([
                RetrievedChunk(
                    chunk_id="xiamen-1",
                    content="前往鼓浪屿前应先核对码头与航线。",
                    source_label="厦门市交通运输局公开信息",
                    score=0.9,
                )
            ])

    planner = Mock()
    result = SafeTravelAgent(
        classifier=Classifier(),
        planner=planner,
        knowledge=Knowledge(),
    ).run("厦门去鼓浪屿怎么安排", trip=None, user_id=None)

    assert result.intent == "travel_knowledge"
    assert "来源：厦门市交通运输局公开信息" in result.reply
    assert result.sources and result.sources[0]["source_label"] == "厦门市交通运输局公开信息"
    planner.invoke.assert_not_called()


def test_standalone_multi_day_weather_question_calls_weather_only() -> None:
    class Classifier:
        def classify(self, *_args):
            return IntentResult(intent="weather_query", confidence=1.0)

    planner = Mock()
    knowledge = Mock()

    class Weather:
        calls = []

        def city_card(self, city_id):
            self.calls.append(city_id)
            from app.schemas import WeatherCard

            return WeatherCard(
                city="厦门",
                status="available",
                summary="晴，25°C",
                report_time=datetime(2026, 8, 13, 9, 30, tzinfo=timezone.utc),
            )

    weather = Weather()
    result = SafeTravelAgent(
        classifier=Classifier(), planner=planner, knowledge=knowledge, weather=weather
    ).run("厦门未来三天天气怎么样？", trip=None, user_id=None)

    assert result.intent == "weather_query"
    assert result.reply == "实时天气（报告时间：2026-08-13 09:30 UTC）：晴，25°C"
    assert weather.calls == ["厦门"]
    planner.invoke.assert_not_called()
    knowledge.answer.assert_not_called()


def test_each_itinerary_date_uses_provider_report_window_then_seasonal_fallback() -> None:
    base = itinerary_factory(days=[
        ItineraryDay(
            date=date(2026, 8, 1),
            morning=Activity(title="上午", start_time="09:00", end_time="11:00"),
            afternoon=Activity(title="下午", start_time="13:00", end_time="15:00"),
            evening=Activity(title="晚上", start_time="18:00", end_time="20:00"),
        ),
        ItineraryDay(
            date=date(2026, 8, 2),
            morning=Activity(title="上午", start_time="09:00", end_time="11:00"),
            afternoon=Activity(title="下午", start_time="13:00", end_time="15:00"),
            evening=Activity(title="晚上", start_time="18:00", end_time="20:00"),
        ),
        ItineraryDay(
            date=date(2026, 8, 3),
            morning=Activity(title="上午", start_time="09:00", end_time="11:00"),
            afternoon=Activity(title="下午", start_time="13:00", end_time="15:00"),
            evening=Activity(title="晚上", start_time="18:00", end_time="20:00"),
        ),
        ItineraryDay(
            date=date(2026, 8, 4),
            morning=Activity(title="上午", start_time="09:00", end_time="11:00"),
            afternoon=Activity(title="下午", start_time="13:00", end_time="15:00"),
            evening=Activity(title="晚上", start_time="18:00", end_time="20:00"),
        ),
    ], end_date=date(2026, 8, 4))

    class Weather:
        calls = []

        def daily_weather(self, destination, travel_date):
            self.calls.append((destination, travel_date))
            if travel_date != date(2026, 8, 4):
                return None
            return ItineraryWeather(
                city="厦门", status="available", summary="晴，25–30°C",
                report_time=datetime(2026, 8, 1, tzinfo=timezone.utc), date=travel_date,
            )

    class Knowledge:
        def answer(self, *_args, **_kwargs):
            return RagAnswer.grounded([
                RetrievedChunk(
                    chunk_id="seasonal", content="夏秋季留意台风和暴雨预警。",
                    source_label="厦门市应急管理局公开提示", score=0.9,
                )
            ])

    weather = Weather()
    itinerary = enrich_itinerary(base, destination="厦门", weather=weather, knowledge=Knowledge())

    assert weather.calls == [("厦门", day.date) for day in base.days]
    assert [day.weather.status for day in itinerary.days if day.weather] == [
        "seasonal", "seasonal", "seasonal", "available"
    ]
    assert "非实时天气" in itinerary.days[0].weather.summary
    assert itinerary.days[3].weather.report_time is not None


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
    itinerary = itinerary_factory().model_copy(update={"notes": ["Hotel live price is CNY 399 per night."]})

    assert {issue.code for issue in validate_itinerary(itinerary, profile_factory(), [])} == {
        "UNSOURCED_DISPLAY_FACT"
    }


def test_planner_preserves_safe_readable_titles_and_notes() -> None:
    candidate = itinerary_factory().model_dump(mode="json")
    candidate["title"] = "杭州慢游两日计划"
    candidate["notes"] = ["每天保留机动时间，按体力调整。"]
    candidate["days"][0]["morning"]["title"] = "西湖沿岸慢行"
    candidate["days"][0]["morning"]["notes"] = ["优先步行，途中安排休息。"]

    planned = Planner(lambda *_: candidate).plan(profile_factory(), [])

    assert planned.title == "杭州慢游两日计划"
    assert planned.notes == ["每天保留机动时间，按体力调整。"]
    assert planned.days[0].morning.title == "西湖沿岸慢行"
    assert planned.days[0].morning.notes == ["优先步行，途中安排休息。"]


def test_citations_must_reference_trusted_evidence_and_disclose_freshness() -> None:
    evidence = TrustedEvidence(
        evidence_id="place-1",
        fact="West Lake is in Hangzhou.",
        source_url="https://photon.komoot.io/api/",
        source_type="trusted_provider",
        fetched_at=datetime(2026, 7, 1, tzinfo=timezone.utc),
    )
    citation = SourceCitation(
        evidence_id="place-1",
        source_url="https://photon.komoot.io/api/",
        source_type="trusted_provider",
        fetched_at=datetime(2026, 7, 1, tzinfo=timezone.utc),
        freshness="Fetched 2026-07-01T00:00:00+00:00; reference only.",
        fact="West Lake is in Hangzhou.",
    )
    itinerary = itinerary_factory(days=[
        ItineraryDay(
            date=date(2026, 8, 1),
            morning=Activity(
                title="Day 1 morning", start_time="09:00", end_time="11:00",
                facts=[FactClaim(text=evidence.fact, evidence_id=evidence.evidence_id)], citations=[citation],
            ),
            afternoon=Activity(title="Day 1 afternoon", start_time="13:00", end_time="16:00"),
            evening=Activity(title="Day 1 evening", start_time="18:00", end_time="20:00"),
        ),
        itinerary_factory().days[1],
    ])

    assert validate_itinerary(itinerary, profile_factory(), [evidence], now=lambda: datetime(2026, 7, 1, tzinfo=timezone.utc)) == []

    invalid = itinerary.model_copy(update={"citations": [citation.model_copy(update={"evidence_id": "made-up"})]})
    assert {issue.code for issue in validate_itinerary(invalid, profile_factory(), [evidence], now=lambda: datetime(2026, 7, 1, tzinfo=timezone.utc))} == {"UNTRUSTED_EVIDENCE"}


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


def test_per_person_budget_is_compared_as_a_trip_total() -> None:
    itinerary = itinerary_factory(budget={
        "transport": 1000, "hotel": 500, "food": 300, "tickets": 100, "reserve": 100, "other": 0,
        "total": 2000, "trip_total": 4000, "currency": "CNY", "traveler_basis": "per_person", "traveler_count": 2,
        "estimate": {"low": 1800, "point": 2000, "high": 2200, "currency": "CNY", "basis": "per_person", "assumption_id": "cost-v1"},
    })

    assert {issue.code for issue in validate_itinerary(itinerary, profile_factory(budget_cny=3500), [])} == {"BUDGET_EXCEEDED"}


def test_claim_metadata_is_derived_from_timestamped_registry_not_model_payload() -> None:
    now = datetime(2026, 7, 2, tzinfo=timezone.utc)
    evidence = TrustedEvidence("hotel-1", "Hotel estimate range is CNY 300–500.", "https://provider.example/hotel", "trusted_provider", now)
    candidate = itinerary_factory(days=[
        ItineraryDay(
            date=date(2026, 8, 1),
            morning=Activity(title="Hotel planning", start_time="09:00", end_time="11:00", claims=[FactClaim(text="Hotel estimate range is CNY 300–500.", evidence_id="hotel-1")]),
            afternoon=itinerary_factory().days[0].afternoon, evening=itinerary_factory().days[0].evening,
        ), itinerary_factory().days[1],
    ])
    repaired = Planner(lambda *_: candidate.model_dump(mode="json"), now=lambda: now).plan(profile_factory(), [evidence])

    assert repaired.days[0].morning.citations[0].source_url == "https://provider.example/hotel"
    assert repaired.days[0].morning.citations[0].fetched_at == now


def test_stale_future_and_free_text_assumptions_fail_closed() -> None:
    now = datetime(2026, 7, 2, tzinfo=timezone.utc)
    stale = TrustedEvidence("place-1", "West Lake is in Hangzhou.", "https://provider.example/place", "trusted_provider", datetime(2026, 6, 20, tzinfo=timezone.utc))
    future = TrustedEvidence("place-2", "West Lake is in Hangzhou.", "https://provider.example/place", "trusted_provider", datetime(2026, 7, 3, tzinfo=timezone.utc))
    with pytest.raises(ValidationError):
        itinerary_factory(assumptions=[{"assumption_id": "cost-v1", "category": "budget", "description": "Hotel price is CNY 399."}])
    with pytest.raises(PlanValidationError):
        Planner(lambda *_: itinerary_factory().model_dump(mode="json"), now=lambda: now).plan(profile_factory(), [stale])
    with pytest.raises(PlanValidationError):
        Planner(lambda *_: itinerary_factory().model_dump(mode="json"), now=lambda: now).plan(profile_factory(), [future])


def test_activity_facts_cannot_authorize_another_activity_or_title() -> None:
    now = datetime(2026, 7, 2, tzinfo=timezone.utc)
    hotel = TrustedEvidence("hotel-1", "Hotel estimate range is CNY 300–500.", "https://provider.example/hotel", "trusted_provider", now)
    restaurant = TrustedEvidence("food-1", "Restaurant opens at 10:00.", "https://provider.example/food", "trusted_provider", now)
    candidate = itinerary_factory(days=[
        ItineraryDay(date=date(2026, 8, 1), morning=Activity(title="Hotel planning", start_time="09:00", end_time="11:00", facts=[FactClaim(text=hotel.fact, evidence_id="hotel-1")]), afternoon=Activity(title="Restaurant", start_time="13:00", end_time="15:00"), evening=Activity(title="Dinner", start_time="18:00", end_time="20:00")), itinerary_factory().days[1],
    ])
    planned = Planner(lambda *_: candidate.model_dump(mode="json"), now=lambda: now).plan(profile_factory(), [hotel, restaurant])
    assert planned.days[0].morning.title == "Hotel planning"
    assert planned.days[0].afternoon.title == "Restaurant"
    assert planned.days[0].afternoon.citations == []


def test_registry_replaces_forged_and_duplicate_citation_metadata() -> None:
    now = datetime(2026, 7, 2, tzinfo=timezone.utc)
    evidence = TrustedEvidence("place-1", "West Lake is in Hangzhou.", "https://provider.example/place", "trusted_provider", now)
    forged = SourceCitation(evidence_id="place-1", source_url="https://evil.example", source_type="official", fetched_at=datetime(2030, 1, 1, tzinfo=timezone.utc), freshness="forged", fact="forged")
    candidate = itinerary_factory(days=[
        ItineraryDay(
            date=date(2026, 8, 1),
            morning=Activity(title="Walk", start_time="09:00", end_time="11:00", facts=[FactClaim(text=evidence.fact, evidence_id="place-1")], citations=[forged, forged]),
            afternoon=itinerary_factory().days[0].afternoon, evening=itinerary_factory().days[0].evening,
        ), itinerary_factory().days[1],
    ])
    planned = Planner(lambda *_: candidate.model_dump(mode="json"), now=lambda: now).plan(profile_factory(), [evidence])
    citations = planned.days[0].morning.citations
    assert len(citations) == 1
    assert citations[0].source_url == evidence.source_url
    assert citations[0].fetched_at == now


def test_estimate_range_must_reference_one_unique_assumption() -> None:
    values = itinerary_factory().model_dump(mode="json")
    values["budget"]["estimate"]["assumption_id"] = "missing"
    with pytest.raises(ValidationError):
        Itinerary.model_validate(values)


@pytest.mark.parametrize("field, value", [("title", "Hotel price is CNY 399."), ("notes", ["Attraction opens at 09:00."])])
def test_top_level_variable_facts_are_rejected_even_when_activity_has_evidence(field: str, value: object) -> None:
    now = datetime(2026, 7, 2, tzinfo=timezone.utc)
    evidence = TrustedEvidence("place-1", "West Lake is in Hangzhou.", "https://provider.example/place", "trusted_provider", now)
    values = itinerary_factory(days=[
        ItineraryDay(date=date(2026, 8, 1), morning=Activity(title="Day 1 morning", start_time="09:00", end_time="11:00", facts=[FactClaim(text=evidence.fact, evidence_id="place-1")], citations=[_canonical_citation(evidence)]), afternoon=itinerary_factory().days[0].afternoon, evening=itinerary_factory().days[0].evening), itinerary_factory().days[1],
    ]).model_dump(mode="json")
    values[field] = value
    itinerary = Itinerary.model_validate(values)

    assert {issue.code for issue in validate_itinerary(itinerary, profile_factory(), [evidence], now=lambda: now)} == {
        "UNSOURCED_DISPLAY_FACT"
    }


def test_duplicate_facts_and_direct_forged_citations_are_not_accepted() -> None:
    now = datetime(2026, 7, 2, tzinfo=timezone.utc)
    evidence = TrustedEvidence("place-1", "West Lake is in Hangzhou.", "https://provider.example/place", "trusted_provider", now)
    fact = FactClaim(text=evidence.fact, evidence_id="place-1")
    candidate = itinerary_factory(days=[
        ItineraryDay(date=date(2026, 8, 1), morning=Activity(title="Walk", start_time="09:00", end_time="11:00", facts=[fact, fact]), afternoon=itinerary_factory().days[0].afternoon, evening=itinerary_factory().days[0].evening), itinerary_factory().days[1],
    ])
    planned = Planner(lambda *_: candidate.model_dump(mode="json"), now=lambda: now).plan(profile_factory(), [evidence])
    assert len(planned.days[0].morning.citations) == 1
    forged = planned.model_copy(deep=True)
    forged.days[0].morning.citations[0] = forged.days[0].morning.citations[0].model_copy(update={"freshness": "forged"})
    assert {issue.code for issue in validate_itinerary(forged, profile_factory(), [evidence], now=lambda: now)} == {"UNTRUSTED_EVIDENCE"}
    values = itinerary_factory().model_dump(mode="json")
    values["assumptions"].append(values["assumptions"][0])
    with pytest.raises(ValidationError):
        Itinerary.model_validate(values)


@pytest.mark.parametrize(
    "display_text",
    [
        "Hotel cost is CNY 399",
        "All rooms are sold out",
        "酒店费用是人民币399元",
        "所有房间均已售罄",
    ],
)
def test_direct_validation_rejects_model_copy_display_text_injection(display_text: str) -> None:
    injected = itinerary_factory().model_copy(update={"title": display_text})

    assert {issue.code for issue in validate_itinerary(injected, profile_factory(), [])} == {
        "UNSOURCED_DISPLAY_FACT"
    }


@pytest.mark.parametrize(
    "display_text",
    [
        "Hotel cost is CNY 399",
        "All rooms are sold out",
        "酒店费用是人民币399元",
        "所有房间均已售罄",
    ],
)
def test_direct_validation_rejects_model_construct_display_text_injection(display_text: str) -> None:
    itinerary = itinerary_factory().model_copy(deep=True)
    original = itinerary.days[0].morning
    injected_activity = Activity.model_construct(
        title=original.title,
        start_time=original.start_time,
        end_time=original.end_time,
        notes=[display_text],
        facts=original.facts,
        citations=original.citations,
    )
    itinerary.days[0] = itinerary.days[0].model_copy(update={"morning": injected_activity})

    assert {issue.code for issue in validate_itinerary(itinerary, profile_factory(), [])} == {
        "UNSOURCED_DISPLAY_FACT"
    }


def test_planner_replaces_unsafe_display_text_but_preserves_safe_readable_text() -> None:
    candidate = itinerary_factory().model_dump(mode="json")
    candidate["title"] = "Hotel cost is CNY 399"
    candidate["notes"] = ["All rooms are sold out"]
    candidate["days"][0]["morning"]["title"] = "酒店费用是人民币399元"
    candidate["days"][0]["morning"]["notes"] = ["所有房间均已售罄"]
    candidate["days"][0]["afternoon"]["title"] = "西湖周边漫步"
    candidate["days"][0]["afternoon"]["notes"] = ["按体力灵活调整路线。"]

    planned = Planner(lambda *_: candidate).plan(profile_factory(), [])

    assert planned.title == "Hangzhou | 2-day itinerary"
    assert planned.notes == []
    assert planned.days[0].morning.title == "Day 1 morning"
    assert planned.days[0].morning.notes == []
    assert planned.days[0].afternoon.title == "西湖周边漫步"
    assert planned.days[0].afternoon.notes == ["按体力灵活调整路线。"]
    assert planned.days[1].evening.title == "Day 2 evening"
    assert validate_itinerary(planned, profile_factory(), []) == []


@pytest.mark.parametrize(
    "target, field, value",
    [
        ("itinerary", "title", ""),
        ("itinerary", "title", "x" * 10_000),
        ("itinerary", "notes", 7),
        ("itinerary", "notes", {"claim": "sold out"}),
        ("activity", "title", 7),
        ("activity", "notes", {"claim": "sold out"}),
    ],
)
def test_planner_canonicalizes_malformed_display_fields_before_schema_validation(
    target: str, field: str, value: object,
) -> None:
    candidate = itinerary_factory().model_dump(mode="json")
    display_owner = candidate if target == "itinerary" else candidate["days"][0]["morning"]
    display_owner[field] = value
    calls: list[list[str] | None] = []

    def generate(_profile: TravelProfile, _providers: object, repair_codes: list[str] | None) -> dict:
        calls.append(repair_codes)
        return candidate

    planned = Planner(generate).plan(profile_factory(), [])

    assert calls == [None]
    assert planned.title == "Hangzhou | 2-day itinerary"
    assert planned.notes == []
    assert planned.days[0].morning.title == "Day 1 morning"
    assert planned.days[0].morning.notes == []


@pytest.mark.parametrize("target, field", [("itinerary", "title"), ("itinerary", "notes"), ("activity", "title"), ("activity", "notes")])
def test_planner_rebuilds_missing_display_fields_without_spending_repair(
    target: str, field: str,
) -> None:
    candidate = itinerary_factory().model_dump(mode="json")
    display_owner = candidate if target == "itinerary" else candidate["days"][0]["morning"]
    del display_owner[field]
    calls: list[list[str] | None] = []

    def generate(_profile: TravelProfile, _providers: object, repair_codes: list[str] | None) -> dict:
        calls.append(repair_codes)
        return candidate

    planned = Planner(generate).plan(profile_factory(), [])

    assert calls == [None]
    assert planned.model_dump(mode="json")["title"] == "Hangzhou | 2-day itinerary"
    assert planned.model_dump(mode="json")["days"][0]["morning"]["title"] == "Day 1 morning"


@pytest.mark.parametrize("malformed", [[], {"days": {}}, {"days": [{"morning": []}]}])
def test_planner_keeps_nonmapping_days_and_activity_structure_fail_closed(malformed: object) -> None:
    calls: list[list[str] | None] = []

    def generate(_profile: TravelProfile, _providers: object, repair_codes: list[str] | None) -> object:
        calls.append(repair_codes)
        return malformed

    with pytest.raises(PlanValidationError) as exc_info:
        Planner(generate).plan(profile_factory(), [])

    assert {issue.code for issue in exc_info.value.issues} == {"SCHEMA_INVALID"}
    assert calls == [None, ["SCHEMA_INVALID"]]


def _canonical_citation(evidence: TrustedEvidence) -> SourceCitation:
    assert evidence.fetched_at is not None
    return SourceCitation(
        evidence_id=evidence.evidence_id,
        source_url=evidence.source_url,
        source_type=evidence.source_type,
        fetched_at=evidence.fetched_at,
        freshness=f"Fetched {evidence.fetched_at.isoformat()}; reference only.",
        fact=evidence.fact,
    )


def test_direct_validation_rejects_model_copy_unknown_fact_id() -> None:
    now = datetime(2026, 7, 2, tzinfo=timezone.utc)
    evidence = TrustedEvidence("place-1", "West Lake is in Hangzhou.", "https://provider.example/place", "trusted_provider", now)
    itinerary = itinerary_factory().model_copy(deep=True)
    activity = itinerary.days[0].morning
    itinerary.days[0].morning = activity.model_copy(update={
        "facts": [FactClaim.model_construct(text=evidence.fact, evidence_id="unknown")],
        "citations": [_canonical_citation(evidence)],
    })

    assert "CLAIM_EVIDENCE_MISMATCH" in {
        issue.code for issue in validate_itinerary(itinerary, profile_factory(), [evidence], now=lambda: now)
    }


def test_direct_validation_rejects_model_construct_wrong_fact_text() -> None:
    now = datetime(2026, 7, 2, tzinfo=timezone.utc)
    evidence = TrustedEvidence("place-1", "West Lake is in Hangzhou.", "https://provider.example/place", "trusted_provider", now)
    itinerary = itinerary_factory().model_copy(deep=True)
    original = itinerary.days[0].morning
    itinerary.days[0].morning = Activity.model_construct(
        title=original.title,
        start_time=original.start_time,
        end_time=original.end_time,
        notes=original.notes,
        facts=[FactClaim.model_construct(text=f"{evidence.fact} ", evidence_id=evidence.evidence_id)],
        citations=[_canonical_citation(evidence)],
    )

    assert "CLAIM_EVIDENCE_MISMATCH" in {
        issue.code for issue in validate_itinerary(itinerary, profile_factory(), [evidence], now=lambda: now)
    }


def test_direct_validation_requires_each_fact_to_have_its_canonical_activity_citation() -> None:
    now = datetime(2026, 7, 2, tzinfo=timezone.utc)
    evidence = TrustedEvidence("place-1", "West Lake is in Hangzhou.", "https://provider.example/place", "trusted_provider", now)
    itinerary = itinerary_factory().model_copy(deep=True)
    activity = itinerary.days[0].morning
    itinerary.days[0].morning = activity.model_copy(update={
        "facts": [FactClaim(text=evidence.fact, evidence_id=evidence.evidence_id)],
        "citations": [],
    })

    assert "UNTRUSTED_EVIDENCE" in {
        issue.code for issue in validate_itinerary(itinerary, profile_factory(), [evidence], now=lambda: now)
    }


def test_direct_validation_applies_provider_timestamp_ttl_to_activity_facts() -> None:
    now = datetime(2026, 7, 2, tzinfo=timezone.utc)
    stale_at = datetime(2026, 6, 20, tzinfo=timezone.utc)
    evidence = TrustedEvidence("place-1", "West Lake is in Hangzhou.", "https://provider.example/place", "trusted_provider", now)
    source = ProviderResult(data=None, source=evidence.source_url, fetched_at=stale_at, evidence=(evidence,))
    itinerary = itinerary_factory().model_copy(deep=True)
    activity = itinerary.days[0].morning
    itinerary.days[0].morning = activity.model_copy(update={
        "facts": [FactClaim(text=evidence.fact, evidence_id=evidence.evidence_id)],
        "citations": [_canonical_citation(evidence)],
    })

    assert "STALE_EVIDENCE" in {
        issue.code for issue in validate_itinerary(itinerary, profile_factory(), [source], now=lambda: now)
    }
