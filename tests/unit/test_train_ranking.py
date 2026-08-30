from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.trains.models import TrainOption, TrainQuery
from app.trains.ranking import (
    get_effective_price,
    get_seat,
    rank_train_options,
)


_CHINA_TIMEZONE = timezone(timedelta(hours=8))


def _option(
    train_no: str,
    *,
    departure_hour: int = 8,
    arrival_hour: int | None = None,
    duration_minutes: int | None = 120,
    bookable: bool | None = None,
    seats: list[dict] | None = None,
) -> TrainOption:
    actual_arrival_hour = arrival_hour if arrival_hour is not None else (departure_hour + 2) % 24
    arrival_date = datetime(2026, 9, 10).date() + timedelta(days=actual_arrival_hour <= departure_hour)
    return TrainOption(
        option_id=f"{train_no}-2026-09-10",
        train_no=train_no,
        departure_station="福州",
        arrival_station="上海",
        departure_at=datetime(2026, 9, 10, departure_hour, tzinfo=_CHINA_TIMEZONE),
        arrival_at=datetime.combine(
            arrival_date,
            datetime.min.time().replace(hour=actual_arrival_hour),
            tzinfo=_CHINA_TIMEZONE,
        ),
        duration_minutes=duration_minutes,
        bookable=bookable,
        seats=seats or [],
    )


def test_cheapest_uses_requested_seat_and_puts_missing_prices_last() -> None:
    query = TrainQuery(
        departure_station="福州",
        arrival_station="上海",
        travel_date="2026-09-10",
        seat_type="二等座",
        preference="cheapest",
    )
    options = [
        _option("G1", seats=[{"seat_name": "二等座", "price_cny": 320}]),
        _option("G2", seats=[{"seat_name": "二等座", "price_cny": 280}]),
        _option("G3", seats=[{"seat_name": "一等座", "price_cny": 100}]),
    ]

    ranked = rank_train_options(options, query)

    assert [option.train_no for option in ranked] == ["G2", "G1", "G3"]
    assert get_effective_price(options[0], "二等座") == Decimal("320")
    assert get_effective_price(options[2], "二等座") is None


def test_cheapest_without_seat_type_uses_lowest_real_seat_price() -> None:
    query = TrainQuery(
        departure_station="福州",
        arrival_station="上海",
        travel_date="2026-09-10",
        preference="cheapest",
    )
    options = [
        _option("G1", seats=[{"seat_name": "一等座", "price_cny": 1000}, {"seat_name": "二等座", "price_cny": 300}]),
        _option("G2", seats=[{"seat_name": "二等座", "price_cny": 500}]),
        _option("G3", seats=[{"seat_name": "二等座", "price_cny": None}]),
    ]

    ranked = rank_train_options(options, query)

    assert [option.train_no for option in ranked] == ["G1", "G2", "G3"]
    assert get_effective_price(options[0], None) == Decimal("300")


def test_cheapest_keeps_equal_prices_in_input_order() -> None:
    query = TrainQuery(
        departure_station="福州",
        arrival_station="上海",
        travel_date="2026-09-10",
        preference="cheapest",
    )
    options = [
        _option("G2", seats=[{"seat_name": "二等座", "price_cny": 300}]),
        _option("G1", seats=[{"seat_name": "二等座", "price_cny": 300}]),
    ]

    ranked = rank_train_options(options, query)

    assert [option.train_no for option in ranked] == ["G2", "G1"]


def test_fastest_puts_unknown_duration_last() -> None:
    query = TrainQuery(
        departure_station="福州",
        arrival_station="上海",
        travel_date="2026-09-10",
        preference="fastest",
    )
    options = [_option("G1", duration_minutes=200), _option("G2", duration_minutes=None), _option("G3", duration_minutes=100)]

    ranked = rank_train_options(options, query)

    assert [option.train_no for option in ranked] == ["G3", "G1", "G2"]


def test_earliest_arrival_orders_cross_day_after_same_day_arrival() -> None:
    query = TrainQuery(
        departure_station="福州",
        arrival_station="上海",
        travel_date="2026-09-10",
        preference="earliest_arrival",
    )
    options = [
        _option("G1", arrival_hour=23),
        _option(
            "G2",
            departure_hour=23,
            arrival_hour=1,
            duration_minutes=120,
        ),
        _option("G3", arrival_hour=12),
    ]
    options[1] = options[1].model_copy(
        update={"arrival_at": datetime(2026, 9, 11, 1, tzinfo=_CHINA_TIMEZONE)}
    )

    ranked = rank_train_options(options, query)

    assert [option.train_no for option in ranked] == ["G3", "G1", "G2"]


def test_default_prioritizes_bookable_or_available_then_departure_time() -> None:
    query = TrainQuery(
        departure_station="福州",
        arrival_station="上海",
        travel_date="2026-09-10",
    )
    options = [
        _option("G1", departure_hour=9, bookable=True),
        _option("G2", departure_hour=8, seats=[{"seat_name": "二等座", "availability": "available"}]),
        _option("G3", departure_hour=7, seats=[{"seat_name": "二等座", "availability": "unknown"}]),
    ]

    ranked = rank_train_options(options, query)

    assert [option.train_no for option in ranked] == ["G2", "G1", "G3"]


def test_default_uses_duration_and_train_number_as_deterministic_tiebreakers() -> None:
    query = TrainQuery(
        departure_station="福州",
        arrival_station="上海",
        travel_date="2026-09-10",
    )
    options = [
        _option("G2", duration_minutes=130),
        _option("G1", duration_minutes=120),
    ]

    ranked = rank_train_options(options, query)

    assert [option.train_no for option in ranked] == ["G1", "G2"]


def test_ranking_does_not_mutate_input_options() -> None:
    query = TrainQuery(
        departure_station="福州",
        arrival_station="上海",
        travel_date="2026-09-10",
        preference="fastest",
    )
    options = [_option("G1", duration_minutes=200), _option("G2", duration_minutes=100)]
    before = [option.model_dump(mode="json") for option in options]

    rank_train_options(options, query)

    assert [option.model_dump(mode="json") for option in options] == before
    assert get_seat(options[0], "不存在") is None
