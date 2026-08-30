from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
import logging
import re
from typing import Callable, Protocol

from app.providers.base import ProviderResult
from app.trains.models import (
    DepartureTimeRange,
    TrainOption,
    TrainQuery,
    TrainSearchResult,
)
from app.trains.ranking import get_seat, rank_train_options
from app.core.logging import operational_context


_CHINA_TIMEZONE = timezone(timedelta(hours=8))
_MAX_QUERY_DAYS = 15
MAX_DISPLAY_OPTIONS = 15
MAX_RECOMMENDATION_CANDIDATES = 5
TRAIN_TIME_RANGES: dict[DepartureTimeRange, tuple[time, time]] = {
    "凌晨": (time(0, 0), time(6, 0)),
    "上午": (time(6, 0), time(12, 0)),
    "下午": (time(12, 0), time(18, 0)),
    "晚上": (time(18, 0), time(23, 59, 59, 999999)),
}


class TrainProvider(Protocol):
    def search(self, query: TrainQuery) -> ProviderResult[tuple[TrainOption, ...]]: ...


class TrainQueryError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class TrainService:
    def __init__(
        self,
        *,
        provider: TrainProvider,
        today: Callable[[], date] | None = None,
    ) -> None:
        self._provider = provider
        self._today = today or _china_today

    def search(self, query: TrainQuery) -> TrainSearchResult:
        normalized_query = _validate_and_normalize(query, self._today())
        provider_result = self._provider.search(normalized_query)
        logging.getLogger("app.train").info(
            "train provider result",
            extra=operational_context(error_code=provider_result.error_code),
        )
        if provider_result.error_code not in (None, "TRAIN_EMPTY"):
            return TrainSearchResult(
                query=normalized_query,
                options=[],
                recommendation_candidates=[],
                fetched_at=provider_result.fetched_at,
                source=provider_result.source,
                status="unavailable",
                warning=_provider_warning(provider_result.error_code),
            )
        if provider_result.data is None:
            return TrainSearchResult(
                query=normalized_query,
                options=[],
                recommendation_candidates=[],
                fetched_at=provider_result.fetched_at,
                source=provider_result.source,
                status="unavailable",
                warning=_provider_warning(provider_result.error_code),
            )

        filtered_options = _filter_options(provider_result.data, normalized_query)
        ranked_options = rank_train_options(filtered_options, normalized_query)
        display_options = list(ranked_options[:MAX_DISPLAY_OPTIONS])
        candidates = display_options[:MAX_RECOMMENDATION_CANDIDATES]
        if display_options:
            return TrainSearchResult(
                query=normalized_query,
                options=display_options,
                recommendation_candidates=candidates,
                fetched_at=provider_result.fetched_at,
                source=provider_result.source,
                status="success",
            )
        return TrainSearchResult(
            query=normalized_query,
            options=[],
            recommendation_candidates=[],
            fetched_at=provider_result.fetched_at,
            source=provider_result.source,
            status="unavailable",
            warning=_filter_empty_warning(normalized_query, provider_result.error_code),
        )


def _validate_and_normalize(query: TrainQuery, today: date) -> TrainQuery:
    departure_station = query.departure_station.strip()
    arrival_station = query.arrival_station.strip()
    if not departure_station:
        raise TrainQueryError("DEPARTURE_STATION_REQUIRED", "出发站不能为空。")
    if not arrival_station:
        raise TrainQueryError("ARRIVAL_STATION_REQUIRED", "到达站不能为空。")
    if departure_station == arrival_station:
        raise TrainQueryError("SAME_STATION", "出发站和到达站不能相同。")
    if query.travel_date < today:
        raise TrainQueryError("DATE_IN_PAST", "车次日期不能早于今天。")
    if query.travel_date > today + timedelta(days=_MAX_QUERY_DAYS - 1):
        raise TrainQueryError("DATE_OUT_OF_RANGE", "车次日期仅支持今天起15天内。")
    seat_type = query.seat_type.strip() if query.seat_type is not None else None
    if seat_type == "":
        seat_type = None
    return query.model_copy(
        update={
            "departure_station": departure_station,
            "arrival_station": arrival_station,
            "seat_type": seat_type,
        }
    )


def _filter_options(
    options: tuple[TrainOption, ...],
    query: TrainQuery,
) -> tuple[TrainOption, ...]:
    return tuple(
        option
        for option in options
        if _matches_station_family(option.departure_station, query.departure_station)
        and _matches_station_family(option.arrival_station, query.arrival_station)
        if _matches_train_types(option, query.train_types)
        and _matches_time_range(option, query.departure_time_range)
        and _matches_seat(option, query.seat_type)
        and _matches_availability(option, query)
    )


def _matches_station_family(actual: str, requested: str) -> bool:
    """Accept a requested city and its named stations, never an unrelated stop."""
    actual_name = re.sub(r"\s+", "", actual).removesuffix("市")
    requested_name = re.sub(r"\s+", "", requested).removesuffix("市")
    return actual_name == requested_name or actual_name.startswith(requested_name)


def _matches_train_types(option: TrainOption, train_types: tuple[str, ...] | None) -> bool:
    if not train_types:
        return True
    train_no = option.train_no.strip().upper()
    return any(train_no.startswith(train_type.upper()) for train_type in train_types)


def _matches_time_range(option: TrainOption, time_range: DepartureTimeRange | None) -> bool:
    if time_range is None:
        return True
    local_time = option.departure_at.astimezone(_CHINA_TIMEZONE).time()
    start, end = TRAIN_TIME_RANGES[time_range]
    return start <= local_time < end


def _matches_seat(option: TrainOption, seat_type: str | None) -> bool:
    return seat_type is None or get_seat(option, seat_type) is not None


def _matches_availability(option: TrainOption, query: TrainQuery) -> bool:
    if not query.require_available:
        return True
    seats = [get_seat(option, query.seat_type)] if query.seat_type else option.seats
    return any(seat is not None and seat.availability == "available" for seat in seats)


def _provider_warning(error_code: str | None) -> str:
    if error_code == "TRAIN_TIMEOUT":
        return "车次服务响应超时，请稍后重试。"
    if error_code == "TRAIN_EMPTY":
        return "没有查询到该路线当天的车次。"
    if error_code == "TRAIN_NO_VALID_OPTIONS":
        return "车次服务返回的数据暂时无法使用，请稍后重试。"
    return "车次服务暂时无法查询，请稍后重试。"


def _filter_empty_warning(query: TrainQuery, provider_error_code: str | None) -> str:
    if provider_error_code == "TRAIN_EMPTY":
        return "没有查询到该路线当天的车次。"
    if query.train_types:
        return "没有符合车型条件的车次。"
    if query.departure_time_range:
        return "没有符合出发时间段的车次。"
    if query.seat_type:
        return "没有符合该席别条件的车次。"
    if query.require_available:
        return "找到符合其他条件的车次，但当前没有返回可用票。"
    return "没有查询到符合条件的车次。"


def _china_today() -> date:
    return datetime.now(_CHINA_TIMEZONE).date()
