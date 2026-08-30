from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.agent.graph import SafeTravelAgent
from app.agent.intent import IntentResult
from app.agent.planning import Planner
from app.providers.aggregate import ProviderBundle
from app.providers.base import ProviderResult
from app.providers.booking_links import BookingLinkBuilder
from app.schemas import Itinerary, TravelProfile
from app.trains.models import TrainOption, TrainQuery, TrainRecommendation, TrainSearchResult
from app.trips.transport import TripTransportResolver


CHINA_TZ = timezone(timedelta(hours=8))


class _Classifier:
    def classify(self, _message: str, _has_trip: bool) -> IntentResult:
        return IntentResult(intent="plan_trip", confidence=1.0)


class _Extractor:
    def extract(self, _message: str, profile: TravelProfile) -> TravelProfile:
        return profile


class _EvidenceProvider:
    def fetch(self, profile: TravelProfile) -> ProviderBundle:
        return ProviderBundle(
            results=(),
            booking_links=BookingLinkBuilder().build(profile),
        )


class _RecordingTrainService:
    def __init__(self, responses: list[TrainSearchResult] | None = None, error: Exception | None = None) -> None:
        self.calls: list[TrainQuery] = []
        self._responses = iter(responses or [])
        self._error = error

    def search(self, query: TrainQuery) -> TrainSearchResult:
        self.calls.append(query)
        if self._error is not None:
            raise self._error
        return next(self._responses)


class _RecordingPlanner:
    def __init__(self) -> None:
        self.received: object | None = None

    def plan(self, _profile: TravelProfile, provider_results: object) -> Itinerary:
        self.received = provider_results
        return _safe_itinerary()


def _profile(**updates: object) -> TravelProfile:
    values: dict[str, object] = {
        "origin": "福州",
        "destination": "上海",
        "start_date": "2026-10-01",
        "end_date": "2026-10-02",
        "travelers": 2,
        "budget_cny": 5000,
    }
    values.update(updates)
    return TravelProfile.model_validate(values)


def _option(
    *,
    option_id: str,
    train_no: str,
    departure_station: str,
    arrival_station: str,
    departure_at: datetime,
    arrival_at: datetime,
    price: int | None = 350,
    availability: str = "available",
) -> TrainOption:
    return TrainOption(
        option_id=option_id,
        train_no=train_no,
        departure_station=departure_station,
        arrival_station=arrival_station,
        departure_at=departure_at,
        arrival_at=arrival_at,
        duration_minutes=int((arrival_at - departure_at).total_seconds() // 60),
        bookable=True if availability == "available" else None,
        seats=[
            {
                "seat_name": "二等座",
                "price_cny": price,
                "remaining_label": "有" if availability == "available" else "待确认",
                "availability": availability,
            }
        ],
    )


def _search_result(option: TrainOption) -> TrainSearchResult:
    return TrainSearchResult(
        query=TrainQuery(
            departure_station=option.departure_station,
            arrival_station=option.arrival_station,
            travel_date=option.departure_at.date(),
        ),
        options=[option],
        recommendation_candidates=[option],
        recommendation=TrainRecommendation(selected_option_id=option.option_id),
        fetched_at=datetime(2026, 9, 30, tzinfo=timezone.utc),
        source="https://www.juhe.cn/docs/api/id/817",
        status="success",
    )


def _outbound() -> TrainOption:
    return _option(
        option_id="G1-outbound",
        train_no="G1",
        departure_station="福州",
        arrival_station="上海虹桥",
        departure_at=datetime(2026, 10, 1, 8, 15, tzinfo=CHINA_TZ),
        arrival_at=datetime(2026, 10, 1, 12, 38, tzinfo=CHINA_TZ),
    )


def _return() -> TrainOption:
    return _option(
        option_id="G2-return",
        train_no="G2",
        departure_station="上海虹桥",
        arrival_station="福州",
        departure_at=datetime(2026, 10, 2, 18, 20, tzinfo=CHINA_TZ),
        arrival_at=datetime(2026, 10, 2, 22, 40, tzinfo=CHINA_TZ),
    )


def _run_confirmed(
    train_service: _RecordingTrainService,
    planner: object | None = None,
    profile: TravelProfile | None = None,
):
    return SafeTravelAgent(
        classifier=_Classifier(),
        extractor=_Extractor(),
        planner=planner or _RecordingPlanner(),
        evidence_provider=_EvidenceProvider(),
        train_service=train_service,
        initial_profile=profile or _profile(),
    ).plan_confirmed(profile or _profile(), None, None, "确认")


def _safe_itinerary() -> Itinerary:
    payload = json.loads(Path("tests/fixtures/task7_itinerary.json").read_text(encoding="utf-8"))
    for day in payload["days"]:
        for slot in ("morning", "afternoon", "evening"):
            day[slot]["facts"] = []
            day[slot]["citations"] = []
    return Itinerary.model_validate(payload)


def _transport_context(*, outbound: TrainOption | None = None, return_option: TrainOption | None = None):
    return SimpleNamespace(
        outbound=outbound,
        return_option=return_option,
        warnings=[],
    )


def _with_seats(option: TrainOption, *seats: dict[str, object]) -> TrainOption:
    return TrainOption.model_validate({**option.model_dump(mode="json"), "seats": list(seats)})


def _provider_input(context: object) -> object:
    return SimpleNamespace(results=(), transport_context=context)


def test_complete_confirmation_queries_outbound_and_return_once() -> None:
    service = _RecordingTrainService([_search_result(_outbound()), _search_result(_return())])

    result = _run_confirmed(service)

    assert result.stage == "planned"
    assert [(call.departure_station, call.arrival_station, call.travel_date) for call in service.calls] == [
        ("福州", "上海", date(2026, 10, 1)),
        ("上海", "福州", date(2026, 10, 2)),
    ]
    assert len(service.calls) == 2


def test_missing_start_date_or_same_route_does_not_query_railway() -> None:
    for profile in (
        _profile(start_date=None),
        _profile(origin="上海", destination="上海"),
    ):
        service = _RecordingTrainService()

        _run_confirmed(service, profile=profile)

        assert service.calls == []


def test_planner_receives_selected_outbound_and_return_train_options() -> None:
    service = _RecordingTrainService([_search_result(_outbound()), _search_result(_return())])
    planner = _RecordingPlanner()

    _run_confirmed(service, planner=planner)

    context = getattr(planner.received, "transport_context")
    assert context.outbound.train_no == "G1"
    assert context.return_option.train_no == "G2"


def test_planner_moves_first_day_after_real_arrival_buffer() -> None:
    context = _transport_context(outbound=_outbound())

    planned = Planner(lambda *_: _safe_itinerary().model_dump(mode="json")).plan(
        _profile(end_date="2026-10-02"),
        _provider_input(context),
    )

    assert planned.days[0].morning.start_time >= "14:08"


def test_planner_ends_last_day_before_real_return_buffer() -> None:
    context = _transport_context(return_option=_return())

    planned = Planner(lambda *_: _safe_itinerary().model_dump(mode="json")).plan(
        _profile(end_date="2026-10-02"),
        _provider_input(context),
    )

    assert planned.days[-1].evening.end_time <= "16:50"


def test_real_second_class_fares_override_model_transport_budget() -> None:
    context = _transport_context(outbound=_outbound(), return_option=_return())
    candidate = _safe_itinerary().model_dump(mode="json")
    candidate["budget"]["transport"] = 867
    candidate["budget"]["total"] = 4167
    candidate["budget"]["trip_total"] = 4167
    candidate["budget"]["estimate"]["point"] = 4167
    candidate["budget"]["estimate"]["high"] = 4500

    planned = Planner(lambda *_: candidate).plan(_profile(budget_cny=8000), _provider_input(context))

    assert planned.budget.transport == 1400
    assert planned.budget.total == 4700
    assert planned.budget.trip_total == 4700


def test_missing_fare_keeps_estimate_and_adds_warning() -> None:
    outbound = _option(
        option_id="G1-no-price",
        train_no="G1",
        departure_station="福州",
        arrival_station="上海虹桥",
        departure_at=datetime(2026, 10, 1, 8, 15, tzinfo=CHINA_TZ),
        arrival_at=datetime(2026, 10, 1, 12, 38, tzinfo=CHINA_TZ),
        price=None,
    )
    service = _RecordingTrainService([_search_result(outbound)])
    result = _run_confirmed(service)

    assert result.stage == "planned"
    assert result.itinerary is not None
    assert result.itinerary.budget.transport == 1200
    assert result.itinerary.budget.transport != 0
    assert any("票价" in warning for warning in result.warnings)


def test_train_failure_still_returns_complete_itinerary_with_reference_warning() -> None:
    service = _RecordingTrainService(error=TimeoutError("fake timeout"))

    result = _run_confirmed(service)

    assert result.stage == "planned"
    assert result.itinerary is not None
    assert "实时车次暂时无法确认" in "；".join(result.warnings)


def test_outbound_success_return_failure_reports_only_return_warning() -> None:
    failed_return = TrainSearchResult(
        query=TrainQuery(
            departure_station="上海",
            arrival_station="福州",
            travel_date=date(2026, 10, 2),
        ),
        options=[],
        recommendation_candidates=[],
        fetched_at=datetime(2026, 9, 30, tzinfo=timezone.utc),
        source="https://www.juhe.cn/docs/api/id/817",
        status="unavailable",
        warning="返程失败",
    )
    service = _RecordingTrainService([_search_result(_outbound()), failed_return])

    result = _run_confirmed(service)

    assert result.transport_context is not None
    assert result.transport_context.outbound is not None
    assert result.transport_context.return_option is None
    assert result.warnings == ["返程实时车次暂时无法确认，请后续核对官方渠道。"]


def test_missing_return_leg_is_partial_pricing_and_keeps_round_trip_estimate() -> None:
    failed_return = TrainSearchResult(
        query=TrainQuery(
            departure_station="上海",
            arrival_station="福州",
            travel_date=date(2026, 10, 2),
        ),
        options=[],
        recommendation_candidates=[],
        fetched_at=datetime(2026, 9, 30, tzinfo=timezone.utc),
        source="https://www.juhe.cn/docs/api/id/817",
        status="unavailable",
        warning="返程失败",
    )
    outbound = _option(
        option_id="G1-outbound-partial",
        train_no="G1",
        departure_station="福州",
        arrival_station="上海虹桥",
        departure_at=datetime(2026, 10, 1, 6, 41, tzinfo=CHINA_TZ),
        arrival_at=datetime(2026, 10, 1, 12, 31, tzinfo=CHINA_TZ),
        price=433.5,
    )
    service = _RecordingTrainService([_search_result(outbound), failed_return])
    candidate = _safe_itinerary().model_dump(mode="json")
    candidate["budget"]["transport"] = 867
    candidate["budget"]["total"] = 4167
    candidate["budget"]["trip_total"] = 4167
    candidate["budget"]["estimate"]["point"] = 4167
    candidate["budget"]["estimate"]["high"] = 4500

    result = _run_confirmed(
        service,
        planner=Planner(lambda *_: candidate),
        profile=_profile(budget_cny=6000),
    )

    assert result.trip_transport is not None
    assert result.trip_transport.pricing_status == "partial"
    assert result.itinerary is not None
    assert result.itinerary.budget.transport == 1734
    assert result.itinerary.budget.transport != 867
    assert result.itinerary.budget.total == 5034
    assert result.itinerary.budget.estimate.point == 5034


def test_return_leg_without_selected_seat_price_is_partial_and_keeps_estimate() -> None:
    outbound = _outbound()
    return_option = _option(
        option_id="G2-return-no-price",
        train_no="G2",
        departure_station="上海虹桥",
        arrival_station="福州",
        departure_at=datetime(2026, 10, 2, 18, 20, tzinfo=CHINA_TZ),
        arrival_at=datetime(2026, 10, 2, 22, 40, tzinfo=CHINA_TZ),
        price=None,
    )
    service = _RecordingTrainService([
        _search_result(outbound),
        _search_result(return_option),
    ])
    candidate = _safe_itinerary().model_dump(mode="json")
    candidate["budget"]["transport"] = 2000
    candidate["budget"]["total"] = 5300
    candidate["budget"]["trip_total"] = 5300
    candidate["budget"]["estimate"]["point"] = 5300
    candidate["budget"]["estimate"]["high"] = 5500

    result = _run_confirmed(
        service,
        planner=Planner(lambda *_: candidate),
        profile=_profile(budget_cny=6000),
    )

    assert result.trip_transport is not None
    assert result.trip_transport.pricing_status == "partial"
    assert result.trip_transport.return_trip is not None
    assert result.trip_transport.return_trip.price is None
    assert result.itinerary is not None
    assert result.itinerary.budget.transport == 1700


def test_unknown_availability_is_not_promoted_to_confirmed_availability() -> None:
    outbound = _option(
        option_id="G1-unknown",
        train_no="G1",
        departure_station="福州",
        arrival_station="上海虹桥",
        departure_at=datetime(2026, 10, 1, 8, 15, tzinfo=CHINA_TZ),
        arrival_at=datetime(2026, 10, 1, 12, 38, tzinfo=CHINA_TZ),
        availability="unknown",
    )
    service = _RecordingTrainService([_search_result(outbound)])
    result = _run_confirmed(service)

    context = getattr(result, "transport_context")
    assert context.outbound.bookable is None
    assert context.outbound.seats[0].availability == "unknown"
    assert any("余票" in warning for warning in result.warnings)


def test_planned_result_exposes_compact_transport_summary_without_train_internals() -> None:
    service = _RecordingTrainService([_search_result(_outbound()), _search_result(_return())])

    result = _run_confirmed(service)

    summary = result.trip_transport
    assert summary is not None
    assert summary.outbound.train_no == "G1"
    assert summary.return_trip.train_no == "G2"
    assert summary.outbound.seat_name == "二等座"
    assert summary.pricing_status == "live"
    assert summary.outbound.source == "https://www.juhe.cn/docs/api/id/817"
    assert summary.outbound.fetched_at is not None
    assert "seats" not in summary.outbound.model_dump()
    assert "option_id" not in summary.outbound.model_dump()
    assert "bookable" not in summary.outbound.model_dump()


def test_real_train_activity_uses_train_times_and_is_not_started_at_buffer_time() -> None:
    context = _transport_context(
        outbound=_option(
            option_id="G1630-outbound",
            train_no="G1630",
            departure_station="福州南",
            arrival_station="上海虹桥",
            departure_at=datetime(2026, 10, 1, 6, 41, tzinfo=CHINA_TZ),
            arrival_at=datetime(2026, 10, 1, 12, 31, tzinfo=CHINA_TZ),
        )
    )
    candidate = _safe_itinerary().model_dump(mode="json")
    candidate["days"][0]["morning"].update(
        {
            "title": "乘坐G1630次列车前往上海",
            "notes": ["从福州南站出发，抵达上海虹桥站，全程约6小时22分钟。"],
            "start_time": "09:00",
            "end_time": "12:00",
        }
    )

    planned = Planner(lambda *_: candidate).plan(
        _profile(end_date="2026-10-02"),
        _provider_input(context),
    )

    assert planned.days[0].morning.start_time >= "14:01"
    assert "G1630" not in planned.days[0].morning.title
    assert planned.days[0].morning.notes == []


def test_buffered_activity_at_1401_is_rendered_as_afternoon_not_morning() -> None:
    context = _transport_context(outbound=_outbound())
    candidate = _safe_itinerary().model_dump(mode="json")

    planned = Planner(lambda *_: candidate).plan(
        _profile(end_date="2026-10-02"),
        _provider_input(context),
    )

    from app.agent.planning import render_itinerary_markdown

    reply = render_itinerary_markdown(planned)

    assert "- 下午 14:08" in reply
    assert "- 上午 14:08" not in reply
@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("两个人明天福州去上海玩两天，一等座", "一等座"),
        ("明天福州去上海，想坐商务座", "商务座"),
        ("明天福州去上海玩两天", None),
    ],
)
def test_complete_trip_extraction_preserves_only_explicit_seat_preference(message: str, expected: str | None) -> None:
    from app.agent.graph import RuleTravelExtractor

    profile = RuleTravelExtractor(reference_date=date(2026, 8, 30)).extract(message, TravelProfile())

    assert getattr(profile, "train_seat", None) == expected


@pytest.mark.parametrize("requested_seat", ["一等座", "商务座", "二等座"])
def test_requested_seat_is_used_for_both_train_queries(requested_seat: str) -> None:
    service = _RecordingTrainService([_search_result(_outbound()), _search_result(_return())])
    profile = SimpleNamespace(
        origin="福州",
        destination="上海",
        start_date="2026-10-01",
        end_date="2026-10-02",
        train_seat=requested_seat,
    )

    TripTransportResolver(service).resolve(profile)

    assert len(service.calls) == 2
    assert [query.seat_type for query in service.calls] == [requested_seat, requested_seat]


def test_missing_seat_preference_defaults_to_second_class_for_both_queries() -> None:
    service = _RecordingTrainService([_search_result(_outbound()), _search_result(_return())])
    profile = SimpleNamespace(
        origin="福州",
        destination="上海",
        start_date="2026-10-01",
        end_date="2026-10-02",
        train_seat=None,
    )

    TripTransportResolver(service).resolve(profile)

    assert [query.seat_type for query in service.calls] == ["二等座", "二等座"]


def test_first_class_prices_override_budget_without_reading_second_class() -> None:
    outbound = _with_seats(
        _outbound(),
        {"seat_name": "一等座", "price_cny": 550, "availability": "available"},
        {"seat_name": "二等座", "price_cny": 350, "availability": "available"},
    )
    return_option = _with_seats(
        _return(),
        {"seat_name": "一等座", "price_cny": 520, "availability": "available"},
        {"seat_name": "二等座", "price_cny": 350, "availability": "available"},
    )
    context = _transport_context(outbound=outbound, return_option=return_option)
    context.seat_type = "一等座"
    candidate = _safe_itinerary().model_dump(mode="json")
    candidate["budget"]["transport"] = 2000
    candidate["budget"]["total"] = 5300
    candidate["budget"]["trip_total"] = 5300
    candidate["budget"]["estimate"]["point"] = 5300
    candidate["budget"]["estimate"]["high"] = 5500

    planned = Planner(lambda *_: candidate).plan(_profile(budget_cny=8000), _provider_input(context))

    assert planned.budget.transport == 2140


def test_business_class_prices_override_budget() -> None:
    outbound = _with_seats(
        _outbound(),
        {"seat_name": "商务座", "price_cny": 900, "availability": "available"},
        {"seat_name": "二等座", "price_cny": 350, "availability": "available"},
    )
    return_option = _with_seats(
        _return(),
        {"seat_name": "商务座", "price_cny": 880, "availability": "available"},
        {"seat_name": "二等座", "price_cny": 350, "availability": "available"},
    )
    context = _transport_context(outbound=outbound, return_option=return_option)
    context.seat_type = "商务座"
    candidate = _safe_itinerary().model_dump(mode="json")
    candidate["budget"]["transport"] = 2000
    candidate["budget"]["total"] = 5300
    candidate["budget"]["trip_total"] = 5300
    candidate["budget"]["estimate"]["point"] = 5300
    candidate["budget"]["estimate"]["high"] = 5500

    planned = Planner(lambda *_: candidate).plan(_profile(budget_cny=8000), _provider_input(context))

    assert planned.budget.transport == 3560


def test_requested_first_class_without_price_does_not_fall_back_to_second_class() -> None:
    outbound = _with_seats(
        _outbound(),
        {"seat_name": "一等座", "price_cny": None, "availability": "unknown"},
        {"seat_name": "二等座", "price_cny": 350, "availability": "available"},
    )
    service = _RecordingTrainService([_search_result(outbound)])
    profile = _profile(train_seat="一等座")

    result = _run_confirmed(service, profile=profile)

    assert result.itinerary is not None
    assert result.itinerary.budget.transport == 1200
    assert result.itinerary.budget.transport != 0
    assert any("一等座" in warning and "估算" in warning for warning in result.warnings)


def test_requested_seat_availability_is_read_from_that_same_train_seat() -> None:
    outbound = _with_seats(
        _outbound(),
        {"seat_name": "一等座", "price_cny": 550, "availability": "unknown"},
        {"seat_name": "二等座", "price_cny": 350, "availability": "available"},
    )
    service = _RecordingTrainService([_search_result(outbound)])
    profile = _profile(train_seat="一等座")

    result = _run_confirmed(service, profile=profile)

    assert any("一等座" in warning and "余票状态未确认" in warning for warning in result.warnings)


@pytest.mark.parametrize("status", ["unavailable"])
def test_no_trains_still_returns_complete_itinerary(status: str) -> None:
    query = TrainQuery(
        departure_station="福州",
        arrival_station="上海",
        travel_date=date(2026, 10, 1),
    )
    service = _RecordingTrainService(
        [
            TrainSearchResult(
                query=query,
                options=[],
                recommendation_candidates=[],
                fetched_at=datetime(2026, 9, 30, tzinfo=timezone.utc),
                source="https://www.juhe.cn/docs/api/id/817",
                status=status,
                warning="没有查询到该路线当天的车次。",
            )
        ]
    )

    result = _run_confirmed(service)

    assert result.stage == "planned"
    assert result.itinerary is not None
    assert "实时车次暂时无法确认" in "；".join(result.warnings)
