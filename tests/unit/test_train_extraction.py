from datetime import date

import pytest

from app.agent.train_extraction import TrainQueryExtractor


def extract(message: str):
    return TrainQueryExtractor(reference_date=date(2026, 8, 30)).extract(message)


@pytest.mark.parametrize(
    ("message", "departure", "arrival", "travel_date"),
    [
        ("明天福州到上海", "福州", "上海", date(2026, 8, 31)),
        ("后天福州去上海", "福州", "上海", date(2026, 9, 1)),
        ("从福州到上海", "福州", "上海", None),
        ("从福州去上海", "福州", "上海", None),
        ("福州前往上海，查明天车次", "福州", "上海", date(2026, 8, 31)),
    ],
)
def test_extracts_supported_routes_and_relative_dates(message, departure, arrival, travel_date) -> None:
    result = extract(message)
    assert result.departure_station == departure
    assert result.arrival_station == arrival
    assert result.travel_date == travel_date


@pytest.mark.parametrize(
    ("message", "travel_date"),
    [
        ("福州到上海 2026-09-10 的车", date(2026, 9, 10)),
        ("福州到上海 2026年9月10日的车", date(2026, 9, 10)),
        ("福州到上海 9月10日的车", date(2026, 9, 10)),
        ("福州到上海 9月10号的车", date(2026, 9, 10)),
    ],
)
def test_extracts_absolute_dates_without_rolling_past_dates_forward(message, travel_date) -> None:
    assert extract(message).travel_date == travel_date


@pytest.mark.parametrize(
    ("message", "train_types"),
    [
        ("明天福州到上海有哪些高铁", ("G",)),
        ("明天福州到上海有哪些动车", ("D",)),
        ("明天福州到上海有哪些城际", ("C",)),
        ("明天福州到上海高铁或动车都可以", ("G", "D")),
        ("明天福州到上海有哪些车", None),
    ],
)
def test_extracts_train_types_without_defaulting_to_high_speed(message, train_types) -> None:
    assert extract(message).train_types == train_types


@pytest.mark.parametrize(
    ("term", "expected"),
    [("凌晨", "凌晨"), ("上午", "上午"), ("早上", "上午"), ("下午", "下午"), ("晚上", "晚上")],
)
def test_extracts_supported_departure_time_ranges(term, expected) -> None:
    assert extract(f"明天福州到上海{term}出发").departure_time_range == expected


@pytest.mark.parametrize(
    ("term", "expected"),
    [("二等座", "二等座"), ("二等", "二等座"), ("一等", "一等座"), ("商务", "商务座")],
)
def test_normalizes_supported_seat_names(term, expected) -> None:
    assert extract(f"明天福州到上海{term}").seat_type == expected


@pytest.mark.parametrize("message", ["明天福州到上海有票", "明天福州到上海还有票吗", "明天福州到上海只看有票的"])
def test_extracts_explicit_available_ticket_requirement(message) -> None:
    assert extract(message).require_available is True


def test_asking_to_check_inventory_does_not_require_available_ticket() -> None:
    assert extract("明天福州到上海查余票").require_available is False


@pytest.mark.parametrize(
    ("message", "preference"),
    [
        ("明天福州到上海最便宜的车", "cheapest"),
        ("明天福州到上海便宜一点", "cheapest"),
        ("明天福州到上海最快的车", "fastest"),
        ("明天福州到上海耗时最短", "fastest"),
        ("明天福州到上海最早到", "earliest_arrival"),
        ("明天福州到上海最便宜但最快", "fastest"),
        ("明天福州到上海有哪些车", "default"),
    ],
)
def test_extracts_deterministic_preferences(message, preference) -> None:
    assert extract(message).preference == preference


@pytest.mark.parametrize(
    ("message", "missing"),
    [
        ("福州到上海有哪些高铁", ("travel_date",)),
        ("明天去上海有哪些高铁", ("departure_station",)),
        ("明天福州坐高铁", ("arrival_station",)),
    ],
)
def test_reports_only_missing_train_query_fields(message, missing) -> None:
    result = extract(message)
    assert result.query is None
    assert result.missing_fields == missing
