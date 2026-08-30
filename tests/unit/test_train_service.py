from datetime import date, datetime, timedelta, timezone

import pytest

from app.providers.base import ProviderResult
from app.trains.models import TrainOption, TrainQuery
from app.trains.service import TrainQueryError, TrainService


_CHINA_TIMEZONE = timezone(timedelta(hours=8))


def _option(
    train_no: str,
    *,
    departure_hour: int = 8,
    arrival_hour: int | None = None,
    duration_minutes: int | None = 120,
    bookable: bool | None = True,
    seats: list[dict] | None = None,
) -> TrainOption:
    actual_arrival_hour = arrival_hour if arrival_hour is not None else (departure_hour + 2) % 24
    arrival_date = date(2026, 9, 10) + timedelta(days=actual_arrival_hour <= departure_hour)
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


def _query(**overrides) -> TrainQuery:
    values = {
        "departure_station": "福州",
        "arrival_station": "上海",
        "travel_date": date(2026, 9, 10),
    }
    values.update(overrides)
    if values["departure_station"] == "" or values["arrival_station"] == "":
        return TrainQuery.model_construct(**values)
    return TrainQuery(**values)


class StubTrainProvider:
    def __init__(self, result: ProviderResult):
        self.result = result
        self.calls: list[TrainQuery] = []

    def search(self, query: TrainQuery) -> ProviderResult:
        self.calls.append(query)
        return self.result


def _provider_result(options: tuple[TrainOption, ...], *, error_code: str | None = None) -> ProviderResult:
    return ProviderResult(
        options,
        "https://www.juhe.cn/docs/api/id/817",
        datetime(2026, 9, 1, tzinfo=timezone.utc),
        degraded=error_code is not None,
        error_code=error_code,
    )


def test_service_calls_provider_with_trimmed_query_and_returns_candidates() -> None:
    provider = StubTrainProvider(_provider_result((_option("G1"), _option("G2", departure_hour=9))))
    service = TrainService(provider=provider, today=lambda: date(2026, 9, 1))

    result = service.search(_query(departure_station=" 福州 ", arrival_station=" 上海 "))

    assert provider.calls[0].departure_station == "福州"
    assert provider.calls[0].arrival_station == "上海"
    assert result.status == "success"
    assert [option.train_no for option in result.options] == ["G1", "G2"]
    assert [option.train_no for option in result.recommendation_candidates] == ["G1", "G2"]


@pytest.mark.parametrize("travel_date", [date(2026, 9, 1), date(2026, 9, 15)])
def test_service_allows_today_and_fifteenth_day(travel_date: date) -> None:
    provider = StubTrainProvider(_provider_result((_option("G1"),)))
    service = TrainService(provider=provider, today=lambda: date(2026, 9, 1))

    service.search(_query(travel_date=travel_date))

    assert len(provider.calls) == 1


@pytest.mark.parametrize("travel_date", [date(2026, 8, 31), date(2026, 9, 16)])
def test_service_rejects_dates_outside_fifteen_day_window_before_provider(travel_date: date) -> None:
    provider = StubTrainProvider(_provider_result((_option("G1"),)))
    service = TrainService(provider=provider, today=lambda: date(2026, 9, 1))

    with pytest.raises(TrainQueryError):
        service.search(_query(travel_date=travel_date))

    assert provider.calls == []


@pytest.mark.parametrize(
    ("departure_station", "arrival_station"),
    [("", "上海"), ("   ", "上海"), ("福州", ""), ("福州", "   "), ("福州", "福州")],
)
def test_service_rejects_invalid_stations_before_provider(departure_station: str, arrival_station: str) -> None:
    provider = StubTrainProvider(_provider_result((_option("G1"),)))
    service = TrainService(provider=provider, today=lambda: date(2026, 9, 1))

    with pytest.raises(TrainQueryError):
        service.search(_query(departure_station=departure_station, arrival_station=arrival_station))

    assert provider.calls == []


def test_service_filters_multiple_train_types_locally() -> None:
    provider = StubTrainProvider(_provider_result((_option("G1"), _option("D2288"), _option("K800"))))
    service = TrainService(provider=provider, today=lambda: date(2026, 9, 1))

    result = service.search(_query(train_types=("G", "D")))

    assert [option.train_no for option in result.options] == ["D2288", "G1"]


def test_service_supports_c_type_without_changing_original_train_number() -> None:
    provider = StubTrainProvider(_provider_result((_option("C123"), _option("G1"))))
    service = TrainService(provider=provider, today=lambda: date(2026, 9, 1))

    result = service.search(_query(train_types=("C",)))

    assert [option.train_no for option in result.options] == ["C123"]
    assert result.options[0].train_no == "C123"


def test_service_does_not_filter_train_types_when_unspecified() -> None:
    provider = StubTrainProvider(_provider_result((_option("G1"), _option("K800"))))
    service = TrainService(provider=provider, today=lambda: date(2026, 9, 1))

    result = service.search(_query())

    assert [option.train_no for option in result.options] == ["G1", "K800"]


def test_service_applies_time_range_again_after_provider_response() -> None:
    provider = StubTrainProvider(
        _provider_result((_option("G1", departure_hour=5), _option("G2", departure_hour=10)))
    )
    service = TrainService(provider=provider, today=lambda: date(2026, 9, 1))

    result = service.search(_query(departure_time_range="上午"))

    assert [option.train_no for option in result.options] == ["G2"]


def test_service_does_not_apply_time_filter_when_unspecified() -> None:
    provider = StubTrainProvider(
        _provider_result((_option("G1", departure_hour=6), _option("G2", departure_hour=18)))
    )
    service = TrainService(provider=provider, today=lambda: date(2026, 9, 1))

    result = service.search(_query())

    assert [option.train_no for option in result.options] == ["G1", "G2"]


def test_service_filters_to_requested_seat_type() -> None:
    provider = StubTrainProvider(
        _provider_result(
            (
                _option("G1", seats=[{"seat_name": "二等座", "price_cny": 300}]),
                _option("G2", seats=[{"seat_name": "一等座", "price_cny": 500}]),
            )
        )
    )
    service = TrainService(provider=provider, today=lambda: date(2026, 9, 1))

    result = service.search(_query(seat_type="二等座"))

    assert [option.train_no for option in result.options] == ["G1"]


def test_service_rejects_options_outside_requested_city_station_family() -> None:
    valid = _option("G1").model_copy(
        update={"departure_station": "福州南", "arrival_station": "上海虹桥"}
    )
    invalid = _option("G2").model_copy(
        update={"departure_station": "福州南", "arrival_station": "练塘"}
    )
    provider = StubTrainProvider(_provider_result((valid, invalid)))
    service = TrainService(provider=provider, today=lambda: date(2026, 9, 1))

    result = service.search(_query())

    assert [option.train_no for option in result.options] == ["G1"]
    assert result.options[0].arrival_station == "上海虹桥"


def test_service_require_available_excludes_unknown_and_unavailable_seats() -> None:
    provider = StubTrainProvider(
        _provider_result(
            (
                _option("G1", seats=[{"seat_name": "二等座", "availability": "unknown"}]),
                _option("G2", seats=[{"seat_name": "二等座", "availability": "unavailable"}]),
                _option("G3", seats=[{"seat_name": "二等座", "availability": "available"}]),
            )
        )
    )
    service = TrainService(provider=provider, today=lambda: date(2026, 9, 1))

    result = service.search(_query(seat_type="二等座", require_available=True))

    assert [option.train_no for option in result.options] == ["G3"]


@pytest.mark.parametrize(
    ("error_code", "expected_warning"),
    [
        ("TRAIN_TIMEOUT", "车次服务响应超时，请稍后重试。"),
        ("TRAIN_NETWORK_ERROR", "车次服务暂时无法查询，请稍后重试。"),
        ("TRAIN_INVALID_RESPONSE", "车次服务暂时无法查询，请稍后重试。"),
        ("TRAIN_NO_VALID_OPTIONS", "车次服务返回的数据暂时无法使用，请稍后重试。"),
    ],
)
def test_service_maps_provider_failures_without_calling_them_empty_results(
    error_code: str,
    expected_warning: str,
) -> None:
    provider = StubTrainProvider(_provider_result((), error_code=error_code))
    service = TrainService(provider=provider, today=lambda: date(2026, 9, 1))

    result = service.search(_query())

    assert result.status == "unavailable"
    assert result.options == []
    assert result.warning == expected_warning


def test_service_maps_provider_empty_result_to_route_message() -> None:
    provider = StubTrainProvider(_provider_result((), error_code="TRAIN_EMPTY"))
    service = TrainService(provider=provider, today=lambda: date(2026, 9, 1))

    result = service.search(_query())

    assert result.status == "unavailable"
    assert result.warning == "没有查询到该路线当天的车次。"


@pytest.mark.parametrize(
    ("query_overrides", "expected_warning"),
    [
        ({"train_types": ("G",)}, "没有符合车型条件的车次。"),
        ({"departure_time_range": "上午"}, "没有符合出发时间段的车次。"),
        ({"seat_type": "商务座"}, "没有符合该席别条件的车次。"),
        ({"require_available": True}, "找到符合其他条件的车次，但当前没有返回可用票。"),
    ],
)
def test_service_distinguishes_local_filter_empty_results(query_overrides, expected_warning: str) -> None:
    provider = StubTrainProvider(_provider_result((_option("K800", departure_hour=18),)))
    service = TrainService(provider=provider, today=lambda: date(2026, 9, 1))

    result = service.search(_query(**query_overrides))

    assert result.status == "unavailable"
    assert result.warning == expected_warning


def test_service_limits_display_options_to_fifteen_and_candidates_to_five() -> None:
    options = tuple(_option(f"G{index}", departure_hour=8 + index % 10) for index in range(20))
    provider = StubTrainProvider(_provider_result(options))
    service = TrainService(provider=provider, today=lambda: date(2026, 9, 1))

    result = service.search(_query())

    assert len(result.options) == 15
    assert len(result.recommendation_candidates) == 5
    assert [option.option_id for option in result.recommendation_candidates] == [option.option_id for option in result.options[:5]]
