from __future__ import annotations

import asyncio
from contextlib import contextmanager
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation
import logging
import re
from time import monotonic
from threading import Event, Lock, Thread
from typing import Any, Awaitable, Callable, Iterator
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx

from app.core.config import Settings
from app.core.logging import operational_context
from app.providers.base import (
    OperationDeadline,
    ProviderResult,
    UpstreamHttpError,
    UpstreamPayloadError,
    request_json,
    utc_now,
)
from app.trains.models import TrainOption, TrainQuery, TrainSeat


JUHE_TRAIN_URL = "https://apis.juhe.cn/fapigw/train/query"
JUHE_TRAIN_SOURCE = "https://www.juhe.cn/docs/api/id/817"
try:
    _CHINA_TIMEZONE = ZoneInfo("Asia/Shanghai")
except ZoneInfoNotFoundError:
    # Mainland China uses UTC+8 year-round; this keeps minimal deployments
    # without the optional tzdata package semantically equivalent.
    _CHINA_TIMEZONE = timezone(timedelta(hours=8))
_DURATION_CLOCK = re.compile(r"^(?P<hours>\d{1,3}):(?P<minutes>[0-5]\d)$")
_DURATION_TEXT = re.compile(
    r"^(?:(?P<hours>\d+)\s*小时)?\s*(?:(?P<minutes>\d+)\s*分)?$"
)
_SENSITIVE_ASSIGNMENT = re.compile(
    r"(?i)\b(?:api[_-]?key|token|secret|password)\b\s*[:=]\s*[^\s,;]+"
)
_URL_QUERY = re.compile(r"(?i)(https?://[^\s?]+)\?[^\s]+")
JUHE_MIN_REQUEST_INTERVAL_SECONDS = 1.05


class JuheTrainRateLimiter:
    """Process-local async gate for Juhe train request starts."""

    def __init__(
        self,
        *,
        clock: Callable[[], float] = monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        minimum_interval: float = JUHE_MIN_REQUEST_INTERVAL_SECONDS,
    ) -> None:
        self._clock = clock
        self._sleep = sleep
        self._minimum_interval = minimum_interval
        self._next_allowed_at = 0.0
        self._ready = Event()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._lock: asyncio.Lock | None = None
        self._thread = Thread(
            target=self._run_loop,
            name="juhe-train-rate-limit",
            daemon=True,
        )
        self._thread.start()
        if not self._ready.wait(timeout=5):
            raise RuntimeError("Juhe train rate limiter failed to start")

    def _run_loop(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        self._lock = asyncio.Lock()
        self._ready.set()
        loop.run_forever()

    def _run(self, coroutine: Any) -> Any:
        if self._loop is None:
            raise RuntimeError("Juhe train rate limiter is not ready")
        return asyncio.run_coroutine_threadsafe(coroutine, self._loop).result()

    async def _acquire(self) -> None:
        if self._lock is None:
            raise RuntimeError("Juhe train rate limiter is not ready")
        await self._lock.acquire()
        try:
            delay = max(0.0, self._next_allowed_at - self._clock())
            if delay:
                await self._sleep(delay)
        except BaseException:
            self._lock.release()
            raise

    async def _release(self) -> None:
        if self._lock is None:
            raise RuntimeError("Juhe train rate limiter is not ready")
        completed_at = self._clock()
        self._next_allowed_at = max(
            self._next_allowed_at,
            completed_at + self._minimum_interval,
        )
        self._lock.release()

    @contextmanager
    def request_slot(self) -> Iterator[None]:
        self._run(self._acquire())
        try:
            yield
        finally:
            self._run(self._release())


_DEFAULT_RATE_LIMITER: JuheTrainRateLimiter | None = None
_DEFAULT_RATE_LIMITER_LOCK = Lock()


def _default_rate_limiter() -> JuheTrainRateLimiter:
    global _DEFAULT_RATE_LIMITER
    if _DEFAULT_RATE_LIMITER is None:
        with _DEFAULT_RATE_LIMITER_LOCK:
            if _DEFAULT_RATE_LIMITER is None:
                _DEFAULT_RATE_LIMITER = JuheTrainRateLimiter()
    return _DEFAULT_RATE_LIMITER


class JuheTrainProvider:
    """Server-only adapter for Juhe's train timetable API."""

    def __init__(
        self,
        *,
        settings: Settings,
        client: httpx.Client | None = None,
        clock: Callable[[], float] = monotonic,
        rate_limiter: Any | None = None,
    ) -> None:
        self._key = (
            settings.juhe_train_api_key.get_secret_value().strip()
            if settings.juhe_train_api_key is not None
            else ""
        )
        self._timeout_seconds = settings.train_timeout_seconds
        self._client = client or httpx.Client(timeout=self._timeout_seconds)
        self._clock = clock
        self._rate_limiter = rate_limiter or _default_rate_limiter()

    def search(
        self,
        query: TrainQuery,
    ) -> ProviderResult[tuple[TrainOption, ...]]:
        fetched_at = utc_now()
        if not self._key:
            return _failure(fetched_at, "TRAIN_NOT_CONFIGURED")

        deadline = OperationDeadline(
            self._clock() + self._timeout_seconds,
            self._clock,
        )
        try:
            payload = request_json(
                self._client,
                JUHE_TRAIN_URL,
                _request_params(query, self._key),
                deadline,
                request_slot=self._rate_limiter.request_slot,
            )
        except httpx.TimeoutException:
            return _failure(fetched_at, "TRAIN_TIMEOUT")
        except httpx.RequestError:
            return _failure(fetched_at, "TRAIN_NETWORK_ERROR")
        except UpstreamHttpError:
            return _failure(fetched_at, "TRAIN_HTTP_ERROR")
        except UpstreamPayloadError:
            return _failure(fetched_at, "TRAIN_INVALID_RESPONSE")

        error_code = payload.get("error_code")
        if error_code is None:
            if _is_nonzero_business_code(payload.get("resultcode")):
                _log_business_error(payload, normalized_error_code="TRAIN_INVALID_RESPONSE")
            return _failure(fetched_at, "TRAIN_INVALID_RESPONSE")
        if error_code not in (0, "0"):
            _log_business_error(payload, normalized_error_code="TRAIN_PROVIDER_ERROR")
            return _failure(fetched_at, "TRAIN_PROVIDER_ERROR")

        raw_options = payload.get("result")
        if not isinstance(raw_options, list):
            return _failure(fetched_at, "TRAIN_INVALID_RESPONSE")
        if not raw_options:
            return _empty(fetched_at, "TRAIN_EMPTY")

        options: list[TrainOption] = []
        for raw_option in raw_options:
            try:
                options.append(_parse_option(raw_option, query))
            except (TypeError, ValueError, KeyError):
                continue

        if not options:
            return _empty(fetched_at, "TRAIN_NO_VALID_OPTIONS")
        return ProviderResult(tuple(options), JUHE_TRAIN_SOURCE, fetched_at)


def _is_nonzero_business_code(value: Any) -> bool:
    return value is not None and value not in (0, "0")


def _safe_business_code(payload: dict[str, Any]) -> int | float | str | None:
    for field in ("error_code", "resultcode"):
        value = payload.get(field)
        if isinstance(value, bool):
            continue
        if isinstance(value, (int, float)):
            return value
        if isinstance(value, str):
            value = value.strip()
            if value:
                return value[:80]
    return None


def _safe_provider_reason(payload: dict[str, Any]) -> str | None:
    value = payload.get("reason")
    if value is None:
        value = payload.get("message")
    if not isinstance(value, (str, int, float)) or isinstance(value, bool):
        return None
    reason = str(value).strip()
    if not reason:
        return None
    reason = _SENSITIVE_ASSIGNMENT.sub("<redacted>", reason)
    reason = _URL_QUERY.sub(r"\1?<redacted>", reason)
    return reason[:200]


def _log_business_error(
    payload: dict[str, Any],
    *,
    normalized_error_code: str,
) -> None:
    logging.getLogger("app.provider").warning(
        "juhe business error",
        extra=operational_context(
            provider="juhe_train",
            error_code=normalized_error_code,
            provider_business_code=_safe_business_code(payload),
            provider_reason=_safe_provider_reason(payload),
        ),
    )


def _request_params(query: TrainQuery, key: str) -> dict[str, str]:
    params = {
        "key": key,
        "search_type": "1",
        "departure_station": query.departure_station,
        "arrival_station": query.arrival_station,
        "date": query.travel_date.isoformat(),
        "enable_booking": "1",
    }
    # Juhe documents a single example value but does not define a delimiter
    # for combining multiple flags. Leave that case for Service filtering.
    if query.train_types is not None and len(query.train_types) == 1:
        params["filter"] = query.train_types[0]
    if query.departure_time_range is not None:
        params["departure_time_range"] = query.departure_time_range
    return params


def _parse_option(raw: Any, query: TrainQuery) -> TrainOption:
    if not isinstance(raw, dict):
        raise TypeError("train option must be an object")

    train_no = _required_text(raw, "train_no")
    departure_station = _required_text(raw, "departure_station")
    arrival_station = _required_text(raw, "arrival_station")
    departure_clock = _parse_clock(_required_text(raw, "departure_time"))
    arrival_clock = _parse_clock(_required_text(raw, "arrival_time"))
    departure_at = datetime.combine(
        query.travel_date,
        departure_clock,
        tzinfo=_CHINA_TIMEZONE,
    )
    arrival_date = query.travel_date
    if arrival_clock < departure_clock:
        arrival_date += timedelta(days=1)
    arrival_at = datetime.combine(arrival_date, arrival_clock, tzinfo=_CHINA_TIMEZONE)

    duration_minutes = _parse_duration(raw.get("duration"))
    if duration_minutes is None:
        duration_minutes = int((arrival_at - departure_at).total_seconds() // 60)
    if duration_minutes < 0:
        raise ValueError("duration must not be negative")

    return TrainOption(
        option_id=f"{train_no}-{query.travel_date.isoformat()}",
        train_no=train_no,
        departure_station=departure_station,
        arrival_station=arrival_station,
        departure_station_code=_optional_text(raw.get("departure_station_code")),
        arrival_station_code=_optional_text(raw.get("arrival_station_code")),
        departure_at=departure_at,
        arrival_at=arrival_at,
        duration_minutes=duration_minutes,
        bookable=_parse_bookable(raw.get("enable_booking")),
        seats=_parse_seats(raw.get("prices")),
        train_flags=_parse_flags(raw.get("train_flags")),
    )


def _parse_clock(value: str) -> time:
    try:
        return datetime.strptime(value, "%H:%M").time()
    except ValueError:
        raise ValueError("train time must use HH:MM") from None


def _parse_duration(value: Any) -> int | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    clock_match = _DURATION_CLOCK.fullmatch(text)
    if clock_match:
        return int(clock_match.group("hours")) * 60 + int(clock_match.group("minutes"))
    text_match = _DURATION_TEXT.fullmatch(text)
    if not text_match or (text_match.group("hours") is None and text_match.group("minutes") is None):
        return None
    return int(text_match.group("hours") or 0) * 60 + int(text_match.group("minutes") or 0)


def _parse_seats(value: Any) -> list[TrainSeat]:
    if not isinstance(value, list):
        return []
    seats: list[TrainSeat] = []
    for raw_seat in value:
        if not isinstance(raw_seat, dict):
            continue
        seat_name = _optional_text(raw_seat.get("seat_name"))
        if seat_name is None:
            continue
        remaining_label = _label(raw_seat.get("num"))
        seats.append(
            TrainSeat(
                seat_name=seat_name,
                price_cny=_parse_price(raw_seat.get("price")),
                remaining_label=remaining_label,
                availability=_availability(raw_seat.get("num")),
            )
        )
    return seats


def _parse_price(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, str) and not value.strip():
        return None
    try:
        price = Decimal(str(value).strip())
    except (AttributeError, InvalidOperation, ValueError):
        return None
    if not price.is_finite() or price < 0:
        return None
    return price


def _availability(value: Any) -> str:
    if isinstance(value, bool):
        return "unknown"
    if isinstance(value, (int, float, Decimal)):
        if value > 0:
            return "available"
        if value == 0:
            return "unavailable"
        return "unknown"
    if not isinstance(value, str):
        return "unknown"
    text = value.strip()
    if not text:
        return "unknown"
    numeric = _parse_price(text)
    if numeric is not None:
        if numeric > 0:
            return "available"
        if numeric == 0:
            return "unavailable"
    if text in {"有", "有票", "可预订", "充足", "余票充足"}:
        return "available"
    if text in {"无", "无票", "不可预订", "候补", "暂无", "售罄"}:
        return "unavailable"
    return "unknown"


def _parse_bookable(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    if not isinstance(value, str):
        return None
    text = value.strip().upper()
    if text in {"Y", "YES", "1", "TRUE", "可预订", "有"}:
        return True
    if text in {"N", "NO", "0", "FALSE", "不可预订", "无"}:
        return False
    return None


def _parse_flags(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item.strip() for item in value if isinstance(item, str) and item.strip()]


def _required_text(payload: dict, name: str) -> str:
    value = payload.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


def _optional_text(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip()


def _label(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return str(value)
    text = str(value).strip()
    return text or None


def _failure(
    fetched_at: datetime,
    error_code: str,
) -> ProviderResult[tuple[TrainOption, ...]]:
    return ProviderResult(
        None,
        JUHE_TRAIN_SOURCE,
        fetched_at,
        degraded=True,
        error_code=error_code,
    )


def _empty(
    fetched_at: datetime,
    error_code: str,
) -> ProviderResult[tuple[TrainOption, ...]]:
    return ProviderResult(
        (),
        JUHE_TRAIN_SOURCE,
        fetched_at,
        degraded=True,
        error_code=error_code,
    )
