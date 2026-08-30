from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.schemas import ChatResponse, TravelProfile
from app.trains.models import (
    TrainOption,
    TrainQuery,
    TrainRecommendation,
    TrainSearchResult,
    TrainSeat,
)


def _option(**overrides):
    values = {
        "option_id": "G25-2026-09-10",
        "train_no": "G25",
        "departure_station": "北京南",
        "arrival_station": "苏州北",
        "departure_station_code": "VNP",
        "arrival_station_code": "OHH",
        "departure_at": datetime(2026, 9, 10, 18, 4, tzinfo=timezone.utc),
        "arrival_at": datetime(2026, 9, 10, 22, 32, tzinfo=timezone.utc),
        "duration_minutes": 268,
        "bookable": True,
        "seats": [
            {
                "seat_name": "二等座",
                "price_cny": Decimal("627"),
                "remaining_label": "1",
                "availability": "available",
            }
        ],
        "train_flags": ["复兴号"],
    }
    values.update(overrides)
    return values


def test_train_query_keeps_explicit_filters_and_preferences():
    query = TrainQuery(
        departure_station="福州",
        arrival_station="上海",
        travel_date=date(2026, 9, 10),
        train_types=["G"],
        departure_time_range="上午",
        seat_type="二等座",
        preference="cheapest",
    )

    assert query.train_types == ("G",)
    assert query.departure_time_range == "上午"
    assert query.seat_type == "二等座"
    assert query.preference == "cheapest"


def test_train_option_accepts_next_day_arrival_with_timezone_aware_times():
    option = TrainOption(
        **_option(
            departure_at=datetime(2026, 9, 10, 23, 40, tzinfo=timezone.utc),
            arrival_at=datetime(2026, 9, 11, 1, 20, tzinfo=timezone.utc),
            duration_minutes=100,
        )
    )

    assert option.arrival_at.date() == date(2026, 9, 11)
    assert option.arrival_at > option.departure_at


def test_train_seat_allows_missing_price_and_unknown_availability():
    seat = TrainSeat(
        seat_name="二等座",
        price_cny=None,
        remaining_label=None,
        availability="unknown",
    )

    assert seat.price_cny is None
    assert seat.availability == "unknown"


def test_train_search_result_contains_bounded_options_and_recommendation():
    options = [TrainOption(**_option(option_id=f"G{index}-2026-09-10", train_no=f"G{index}")) for index in range(1, 4)]
    result = TrainSearchResult(
        query=TrainQuery(
            departure_station="北京",
            arrival_station="苏州",
            travel_date=date(2026, 9, 10),
        ),
        options=options,
        recommendation=TrainRecommendation(
            selected_option_id=options[0].option_id,
            reason_codes=["time_fit", "shorter_duration"],
        ),
        fetched_at=datetime(2026, 9, 1, 2, 0, tzinfo=timezone.utc),
        source="https://www.juhe.cn/docs/api/id/817",
        status="success",
    )

    assert len(result.options) == 3
    assert result.recommendation.selected_option_id == "G1-2026-09-10"


def test_train_option_rejects_arrival_before_departure():
    with pytest.raises(ValidationError):
        TrainOption(
            **_option(
                departure_at=datetime(2026, 9, 10, 18, 4, tzinfo=timezone.utc),
                arrival_at=datetime(2026, 9, 10, 17, 32, tzinfo=timezone.utc),
            )
        )


def test_chat_response_can_carry_ephemeral_train_result_without_changing_trip_schema():
    option = TrainOption(**_option())
    train_result = TrainSearchResult(
        query=TrainQuery(
            departure_station="北京",
            arrival_station="苏州",
            travel_date=date(2026, 9, 10),
        ),
        options=[option],
        fetched_at=datetime(2026, 9, 1, 2, 0, tzinfo=timezone.utc),
        source="https://www.juhe.cn/docs/api/id/817",
        status="success",
    )
    response = ChatResponse(
        reply="已找到车次。",
        stage="planned",
        profile=TravelProfile(),
        train_result=train_result,
    )

    assert response.train_result is not None
    assert response.train_result.options[0].train_no == "G25"
