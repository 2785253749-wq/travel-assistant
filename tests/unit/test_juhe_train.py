from datetime import date, datetime, timedelta
from decimal import Decimal
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from app.core.config import Settings
from app.providers.juhe_train import JUHE_TRAIN_SOURCE, JuheTrainProvider
from app.trains.models import TrainQuery
from tests.fixtures.providers import RecordingTransport, json_response


def _settings(key: str | None = "test-train-key") -> Settings:
    return Settings(_env_file=None, juhe_train_api_key=key)


def _query(**overrides) -> TrainQuery:
    values = {
        "departure_station": "福州",
        "arrival_station": "上海",
        "travel_date": date(2026, 9, 10),
    }
    values.update(overrides)
    return TrainQuery(**values)


def _train(**overrides) -> dict:
    values = {
        "train_no": "G25",
        "departure_station": "北京南",
        "arrival_station": "苏州北",
        "departure_station_code": "VNP",
        "arrival_station_code": "OHH",
        "departure_time": "18:04",
        "arrival_time": "22:32",
        "duration": "04:28",
        "enable_booking": "Y",
        "prices": [
            {"seat_name": "商务座", "price": 2194, "num": "无"},
            {"seat_name": "一等座", "price": 1003, "num": "无"},
            {"seat_name": "二等座", "price": 627, "num": "1"},
        ],
        "train_flags": ["智能动车组", "复兴号"],
    }
    values.update(overrides)
    return values


def _provider(payload: dict, *, settings: Settings | None = None):
    transport = RecordingTransport([json_response(payload)])
    client = httpx.Client(transport=transport)
    return JuheTrainProvider(
        settings=settings or _settings(),
        client=client,
    ), transport


def test_search_maps_required_and_optional_query_parameters() -> None:
    provider, transport = _provider(
        {"error_code": 0, "result": []},
    )

    result = provider.search(
        _query(train_types=("G",), departure_time_range="上午")
    )

    params = parse_qs(urlparse(str(transport.requests[0].url)).query)
    assert result.source == JUHE_TRAIN_SOURCE
    assert params["key"] == ["test-train-key"]
    assert params["search_type"] == ["1"]
    assert params["departure_station"] == ["福州"]
    assert params["arrival_station"] == ["上海"]
    assert params["date"] == ["2026-09-10"]
    assert params["filter"] == ["G"]
    assert params["enable_booking"] == ["1"]
    assert params["departure_time_range"] == ["上午"]


def test_search_omits_unset_filter_and_time_range() -> None:
    provider, transport = _provider({"error_code": 0, "result": []})

    provider.search(_query())

    params = parse_qs(urlparse(str(transport.requests[0].url)).query)
    assert "filter" not in params
    assert "departure_time_range" not in params


def test_search_does_not_guess_format_for_multiple_train_types() -> None:
    provider, transport = _provider({"error_code": 0, "result": []})

    provider.search(_query(train_types=("G", "D")))

    params = parse_qs(urlparse(str(transport.requests[0].url)).query)
    assert "filter" not in params


def test_search_maps_a_complete_train_to_internal_models() -> None:
    provider, _ = _provider({"error_code": 0, "result": [_train()]})

    result = provider.search(_query())

    assert result.data is not None
    assert len(result.data) == 1
    option = result.data[0]
    assert option.train_no == "G25"
    assert option.departure_station == "北京南"
    assert option.arrival_station == "苏州北"
    assert option.departure_station_code == "VNP"
    assert option.arrival_station_code == "OHH"
    assert option.departure_at == datetime(2026, 9, 10, 18, 4, tzinfo=option.departure_at.tzinfo)
    assert option.arrival_at == datetime(2026, 9, 10, 22, 32, tzinfo=option.arrival_at.tzinfo)
    assert option.departure_at.utcoffset() == timedelta(hours=8)
    assert option.duration_minutes == 268
    assert option.bookable is True
    assert option.train_flags == ["智能动车组", "复兴号"]
    assert [seat.seat_name for seat in option.seats] == ["商务座", "一等座", "二等座"]
    assert option.seats[0].price_cny == Decimal("2194")
    assert option.seats[1].price_cny == Decimal("1003")
    assert option.seats[2].price_cny == Decimal("627")


@pytest.mark.parametrize(
    ("num", "expected"),
    [
        ("有", "available"),
        ("2", "available"),
        (2, "available"),
        ("无", "unavailable"),
        ("0", "unavailable"),
        (0, "unavailable"),
        ("未知", "unknown"),
        (None, "unknown"),
    ],
)
def test_search_normalizes_seat_availability(num, expected: str) -> None:
    provider, _ = _provider(
        {"error_code": 0, "result": [_train(prices=[{"seat_name": "二等座", "price": 627, "num": num}])]},
    )

    result = provider.search(_query())

    assert result.data[0].seats[0].availability == expected


def test_search_preserves_train_when_price_or_prices_are_missing() -> None:
    provider, _ = _provider(
        {
            "error_code": 0,
            "result": [
                _train(
                    prices=[
                        {"seat_name": "二等座", "price": "not-a-price", "num": "未知"},
                        {"seat_name": "一等座", "price": None, "num": "有"},
                    ]
                ),
                _train(train_no="D2288", prices=None, train_flags=None),
            ],
        }
    )

    result = provider.search(_query())

    assert result.data is not None
    assert len(result.data) == 2
    assert result.data[0].seats[0].price_cny is None
    assert result.data[0].seats[0].availability == "unknown"
    assert result.data[0].seats[1].price_cny is None
    assert result.data[0].seats[1].availability == "available"
    assert result.data[1].seats == []
    assert result.data[1].train_flags == []


def test_search_handles_cross_day_arrival_and_falls_back_to_time_difference() -> None:
    provider, _ = _provider(
        {
            "error_code": 0,
            "result": [_train(departure_time="23:40", arrival_time="00:30", duration="unknown")],
        }
    )

    result = provider.search(_query())

    option = result.data[0]
    assert option.arrival_at.date() == date(2026, 9, 11)
    assert option.duration_minutes == 50


def test_search_skips_bad_rows_but_keeps_valid_rows() -> None:
    provider, _ = _provider(
        {
            "error_code": 0,
            "result": [
                _train(train_no="BROKEN", arrival_time=None),
                _train(train_no="G26"),
            ],
        }
    )

    result = provider.search(_query())

    assert result.data is not None
    assert [option.train_no for option in result.data] == ["G26"]


def test_search_returns_degraded_empty_result_when_no_row_is_valid() -> None:
    provider, _ = _provider(
        {"error_code": 0, "result": [{"train_no": "BROKEN"}]}
    )

    result = provider.search(_query())

    assert result.data == ()
    assert result.degraded is True
    assert result.error_code == "TRAIN_NO_VALID_OPTIONS"


def test_search_returns_degraded_empty_result_for_empty_provider_result() -> None:
    provider, _ = _provider({"error_code": 0, "result": []})

    result = provider.search(_query())

    assert result.data == ()
    assert result.degraded is True
    assert result.error_code == "TRAIN_EMPTY"


@pytest.mark.parametrize(
    ("settings", "responses", "error_code", "request_count"),
    [
        (_settings(None), [], "TRAIN_NOT_CONFIGURED", 0),
        (_settings(), [httpx.TimeoutException("timed out")], "TRAIN_TIMEOUT", 1),
        (_settings(), [httpx.ConnectError("offline"), httpx.ConnectError("offline")], "TRAIN_NETWORK_ERROR", 2),
        (_settings(), [json_response({}, status_code=429)], "TRAIN_HTTP_ERROR", 1),
        (_settings(), [json_response({}, status_code=503), json_response({}, status_code=503)], "TRAIN_HTTP_ERROR", 2),
        (_settings(), [httpx.Response(200, content=b"not-json")], "TRAIN_INVALID_RESPONSE", 1),
    ],
)
def test_search_normalizes_transport_failures(
    settings: Settings,
    responses: list,
    error_code: str,
    request_count: int,
) -> None:
    transport = RecordingTransport(responses)
    provider = JuheTrainProvider(settings=settings, client=httpx.Client(transport=transport))

    result = provider.search(_query())

    assert result.data is None
    assert result.degraded is True
    assert result.error_code == error_code
    assert len(transport.requests) == request_count


def test_search_normalizes_provider_business_error_without_exposing_reason() -> None:
    provider, _ = _provider(
        {"error_code": 281701, "reason": "secret provider detail", "result": []}
    )

    result = provider.search(_query())

    assert result.data is None
    assert result.degraded is True
    assert result.error_code == "TRAIN_PROVIDER_ERROR"
    assert "secret provider detail" not in str(result)


@pytest.mark.parametrize(
    "payload",
    [
        {"error_code": 0},
        {"error_code": 0, "result": {}},
        {"result": []},
    ],
)
def test_search_rejects_missing_or_malformed_result_payload(payload: dict) -> None:
    provider, _ = _provider(payload)

    result = provider.search(_query())

    assert result.data is None
    assert result.degraded is True
    assert result.error_code == "TRAIN_INVALID_RESPONSE"


def test_search_never_places_api_key_in_error_text_or_logs(caplog) -> None:
    secret = "test-secret-train-key"
    provider, _ = _provider(
        {"error_code": 281701, "reason": "query failed"},
        settings=_settings(secret),
    )

    result = provider.search(_query())

    assert secret not in str(result)
    assert secret not in caplog.text
