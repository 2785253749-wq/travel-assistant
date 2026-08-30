"""Server-owned AI model-call accounting and circuit-breaker controls.

The in-memory repository is deliberately lock-protected for local/test use.
Production deployments can use the accompanying SQL RPC migration so each
reservation is made in one database transaction across web workers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
import logging
from threading import RLock
from time import monotonic, sleep
from typing import Any, Callable, Protocol
from contextlib import contextmanager
from contextvars import ContextVar
from uuid import uuid4

from app.core.errors import AppError
from app.core.logging import operational_context


MODEL_CALL_SLOTS_PER_REQUEST = 2
MODEL_RETRY_BACKOFF_SECONDS = 0.35


class ProviderUnavailable(Exception):
    """Stable internal signal for an unavailable or unconfigured provider."""

    def __init__(self, code: str = "AI_UNAVAILABLE") -> None:
        self.code = code
        super().__init__(code)


def classify_provider_error(error: Exception) -> str:
    """Normalize vendor failures without ever returning their message/body."""
    status = getattr(error, "status_code", getattr(error, "status", None))
    if str(status) == "429":
        return "AI_PROVIDER_RATE_LIMITED"
    if isinstance(status, int) and status >= 500:
        return "AI_PROVIDER_UNAVAILABLE"
    return "AI_PROVIDER_UNAVAILABLE"


class ProviderCircuitBreaker:
    """Small fail-closed circuit for repeated upstream service failures."""

    def __init__(self, failure_threshold: int = 3, cooldown: timedelta = timedelta(seconds=30), clock: Callable[[], datetime] | None = None) -> None:
        self._failure_threshold = failure_threshold
        self._cooldown = cooldown
        self._clock = clock or (lambda: datetime.now(UTC))
        self._failures = 0
        self._open_until: datetime | None = None
        self._lock = RLock()

    def allow(self) -> bool:
        with self._lock:
            if self._open_until is None:
                return True
            if self._clock() >= self._open_until:
                self._open_until = None
                self._failures = 0
                return True
            return False

    def record_failure(self, code: str) -> None:
        if code not in {"AI_PROVIDER_RATE_LIMITED", "AI_PROVIDER_UNAVAILABLE"}:
            return
        with self._lock:
            self._failures += 1
            if self._failures >= self._failure_threshold:
                self._open_until = self._clock() + self._cooldown

    def record_success(self) -> None:
        with self._lock:
            self._failures = 0
            self._open_until = None


@dataclass
class ModelUsage:
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    _reservation: Any | None = field(default=None, repr=False)

    def record_attempt(self) -> None:
        if self._reservation is not None:
            self._reservation.admit_model_call()
        self.calls += 1

    def record_tokens(self, response: object) -> None:
        metadata = getattr(response, "usage_metadata", None) or getattr(response, "usage", None) or {}
        self.input_tokens += max(0, int(metadata.get("input_tokens", metadata.get("prompt_tokens", 0)) or 0))
        self.output_tokens += max(0, int(metadata.get("output_tokens", metadata.get("completion_tokens", 0)) or 0))

    def record(self, response: object) -> None:
        """Compatibility helper for callers that already have a response."""
        self.record_attempt()
        self.record_tokens(response)


_model_usage: ContextVar[ModelUsage | None] = ContextVar("model_usage", default=None)


@contextmanager
def model_usage_scope(reservation: Any | None = None):
    usage = ModelUsage(_reservation=reservation)
    token = _model_usage.set(usage)
    try:
        yield usage
    finally:
        _model_usage.reset(token)


class ModelGateway:
    """The only production DeepSeek invocation boundary."""

    def __init__(self, factory: Callable[[], Any], breaker: ProviderCircuitBreaker | None = None) -> None:
        self._factory = factory
        self._breaker = breaker or ProviderCircuitBreaker()

    def invoke(self, messages: Any, *, structured: Any | None = None) -> Any:
        if not self._breaker.allow():
            raise ProviderUnavailable("AI_CIRCUIT_OPEN")
        for attempt in range(1, 3):
            started = monotonic()
            logging.getLogger("app.model").info(
                f"model attempt={attempt} start",
                extra=operational_context(
                    provider="deepseek",
                    attempt=attempt,
                ),
            )
            try:
                client = self._factory()
                if structured is not None:
                    client = client.with_structured_output(structured, method="json_mode")
                collector = _model_usage.get()
                if collector is not None:
                    collector.record_attempt()
                response = client.invoke(messages)
            except ProviderUnavailable:
                raise
            except AppError:
                raise
            except Exception as exc:
                code = classify_provider_error(exc)
                status = _provider_status(exc)
                safe_status = status if isinstance(status, int) and 100 <= status <= 599 else None
                elapsed = round(monotonic() - started, 3)
                logging.getLogger("app.model").warning(
                    "model_provider_failure",
                    extra=operational_context(
                        provider="deepseek",
                        provider_status=safe_status,
                        error_code=code,
                        exception_type=type(exc).__name__,
                        attempt=attempt,
                        elapsed_seconds=elapsed,
                    ),
                )
                self._breaker.record_failure(code)
                if attempt == 1 and _is_retryable_model_error(exc):
                    logging.getLogger("app.model").warning(
                        "model attempt=1 transient_failure",
                        extra=operational_context(
                            provider="deepseek",
                            provider_status=safe_status,
                            error_code=code,
                            attempt=attempt,
                            elapsed_seconds=elapsed,
                        ),
                    )
                    sleep(MODEL_RETRY_BACKOFF_SECONDS)
                    continue
                logging.getLogger("app.model").warning(
                    f"model attempt={attempt} failure",
                    extra=operational_context(
                        provider="deepseek",
                        provider_status=safe_status,
                        error_code=code,
                        attempt=attempt,
                        elapsed_seconds=elapsed,
                    ),
                )
                raise ProviderUnavailable("AI_RATE_LIMITED" if code == "AI_PROVIDER_RATE_LIMITED" else "AI_UNAVAILABLE") from None
            self._breaker.record_success()
            if collector is not None:
                collector.record_tokens(response)
            logging.getLogger("app.model").info(
                f"model attempt={attempt} success",
                extra=operational_context(
                    provider="deepseek",
                    attempt=attempt,
                    elapsed_seconds=round(monotonic() - started, 3),
                ),
            )
            return response
        raise AssertionError("unreachable")


def _provider_status(error: Exception) -> int | None:
    status = getattr(error, "status_code", getattr(error, "status", None))
    if isinstance(status, int):
        return status
    response = getattr(error, "response", None)
    response_status = getattr(response, "status_code", None)
    return response_status if isinstance(response_status, int) else None


def _is_retryable_model_error(error: Exception) -> bool:
    name = type(error).__name__.lower()
    if "authentication" in name:
        return False
    status = _provider_status(error)
    if status is not None:
        return status in {500, 502, 503, 504}
    try:
        import httpx
    except ImportError:
        httpx = None
    retryable_types = (TimeoutError, ConnectionError)
    if httpx is not None:
        retryable_types = retryable_types + (httpx.TimeoutException, httpx.RequestError)
    return isinstance(error, retryable_types)


_model_gateways: dict[int, ModelGateway] = {}


def get_model_gateway(factory: Callable[[], Any]) -> ModelGateway:
    key = id(factory)
    if key not in _model_gateways:
        _model_gateways[key] = ModelGateway(factory)
    return _model_gateways[key]


@dataclass(frozen=True)
class UsageCount:
    request_count: int = 0
    pending: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    model_calls: int = 0
    estimated_cost_micros: int = 0

    @property
    def reserved_model_calls(self) -> int:
        return self.model_calls + self.pending

    @property
    def reserved_count(self) -> int:
        """Compatibility alias for the quota-bearing model-call total."""
        return self.reserved_model_calls


@dataclass(frozen=True)
class ReserveResult:
    reservation_id: str | None
    failure_reason: str | None


@dataclass(frozen=True)
class CallAdmissionResult:
    admitted: bool
    failure_reason: str | None


class UsageRepository(Protocol):
    def get_daily(self, user_key: str, day: date) -> UsageCount: ...
    def get_global_daily(self, day: date) -> UsageCount: ...
    def reserve(self, user_key: str, day: date, user_limit: int, global_limit: int) -> ReserveResult: ...
    def admit_model_call(
        self,
        reservation_id: str,
        user_key: str,
        reservation_day: date,
        call_day: date,
        user_limit: int,
        global_limit: int,
    ) -> CallAdmissionResult: ...
    def commit(
        self,
        reservation_id: str,
        user_key: str,
        day: date,
        input_tokens: int,
        output_tokens: int,
        model_calls: int,
        estimated_cost_micros: int,
    ) -> None: ...
    def rollback(self, reservation_id: str, user_key: str, day: date) -> None: ...


@dataclass
class _UsageReservationState:
    user_key: str
    reservation_day: date
    expires_at: datetime
    status: str
    reserved_model_calls: int
    incurred_model_calls: int = 0


class InMemoryUsageRepository:
    """Atomic reference implementation used without any network dependency."""

    def __init__(self, *, clock: Callable[[], datetime] | None = None) -> None:
        self._lock = RLock()
        self._users: dict[tuple[str, date], UsageCount] = {}
        self._global: dict[date, UsageCount] = {}
        self._reservations: dict[str, _UsageReservationState] = {}
        self._clock = clock or (lambda: datetime.now(UTC))

    def get_daily(self, user_key: str, day: date) -> UsageCount:
        with self._lock:
            return self._users.get((user_key, day), UsageCount())

    def get_global_daily(self, day: date) -> UsageCount:
        with self._lock:
            return self._global.get(day, UsageCount())

    def set_user_count(self, user_key: str, request_count: int, *, day: date) -> None:
        with self._lock:
            current = self._users.get((user_key, day), UsageCount())
            self._users[(user_key, day)] = UsageCount(
                request_count,
                current.pending,
                current.input_tokens,
                current.output_tokens,
                current.model_calls,
                current.estimated_cost_micros,
            )
            global_count = self._global.get(day, UsageCount())
            self._global[day] = UsageCount(
                request_count,
                global_count.pending,
                global_count.input_tokens,
                global_count.output_tokens,
                global_count.model_calls,
                global_count.estimated_cost_micros,
            )

    def reserve(self, user_key: str, day: date, user_limit: int, global_limit: int) -> ReserveResult:
        with self._lock:
            now = self._clock()
            self._cleanup_expired(day, now)
            user = self._users.get((user_key, day), UsageCount())
            global_count = self._global.get(day, UsageCount())
            if user.reserved_model_calls + MODEL_CALL_SLOTS_PER_REQUEST > user_limit:
                return ReserveResult(None, "user_limit")
            if global_count.reserved_model_calls + MODEL_CALL_SLOTS_PER_REQUEST > global_limit:
                return ReserveResult(None, "global_limit")
            self._users[(user_key, day)] = UsageCount(
                user.request_count,
                user.pending + MODEL_CALL_SLOTS_PER_REQUEST,
                user.input_tokens,
                user.output_tokens,
                user.model_calls,
                user.estimated_cost_micros,
            )
            self._global[day] = UsageCount(
                global_count.request_count,
                global_count.pending + MODEL_CALL_SLOTS_PER_REQUEST,
                global_count.input_tokens,
                global_count.output_tokens,
                global_count.model_calls,
                global_count.estimated_cost_micros,
            )
            reservation_id = str(uuid4())
            self._reservations[reservation_id] = _UsageReservationState(
                user_key=user_key,
                reservation_day=day,
                expires_at=now + timedelta(minutes=5),
                status="reserved",
                reserved_model_calls=MODEL_CALL_SLOTS_PER_REQUEST,
            )
            return ReserveResult(reservation_id, None)

    def _cleanup_expired(self, day: date, now: datetime) -> None:
        for reservation in self._reservations.values():
            if (
                reservation.reservation_day == day
                and reservation.status == "reserved"
                and reservation.expires_at <= now
            ):
                # An expired reservation may already have incurred a provider call.
                # Keep its worst-case slots pending until a late settlement arrives;
                # releasing them would allow the daily ceiling to be oversold.
                reservation.status = "expired"

    def admit_model_call(
        self,
        reservation_id: str,
        user_key: str,
        reservation_day: date,
        call_day: date,
        user_limit: int,
        global_limit: int,
    ) -> CallAdmissionResult:
        with self._lock:
            reservation = self._reservations.get(reservation_id)
            if (
                reservation is None
                or reservation.user_key != user_key
                or reservation.reservation_day != reservation_day
                or reservation.status != "reserved"
            ):
                return CallAdmissionResult(False, "reservation_unavailable")
            if reservation.expires_at <= self._clock():
                reservation.status = "expired"
                return CallAdmissionResult(False, "reservation_expired")
            if reservation.incurred_model_calls >= reservation.reserved_model_calls:
                return CallAdmissionResult(False, "reservation_exhausted")

            source_user = self._users.get((user_key, reservation_day), UsageCount())
            source_global = self._global.get(reservation_day, UsageCount())
            if call_day == reservation_day:
                self._users[(user_key, reservation_day)] = self._incur_reserved_call(
                    source_user
                )
                self._global[reservation_day] = self._incur_reserved_call(
                    source_global
                )
            else:
                target_user = self._users.get((user_key, call_day), UsageCount())
                target_global = self._global.get(call_day, UsageCount())
                if target_user.reserved_model_calls + 1 > user_limit:
                    return CallAdmissionResult(False, "user_limit")
                if target_global.reserved_model_calls + 1 > global_limit:
                    return CallAdmissionResult(False, "global_limit")
                self._users[(user_key, reservation_day)] = self._rollback_count(
                    source_user, 1
                )
                self._global[reservation_day] = self._rollback_count(source_global, 1)
                self._users[(user_key, call_day)] = self._record_model_call(
                    target_user
                )
                self._global[call_day] = self._record_model_call(target_global)

            reservation.incurred_model_calls += 1
            return CallAdmissionResult(True, None)

    def commit(
        self,
        reservation_id: str,
        user_key: str,
        day: date,
        input_tokens: int,
        output_tokens: int,
        model_calls: int,
        estimated_cost_micros: int,
    ) -> None:
        with self._lock:
            reservation = self._reservations.get(reservation_id)
            if (
                reservation is None
                or (reservation.user_key, reservation.reservation_day)
                != (user_key, day)
                or reservation.status not in {"reserved", "expired"}
            ):
                return
            reserved_model_calls = reservation.reserved_model_calls
            if model_calls != reservation.incurred_model_calls:
                raise ValueError("actual model calls do not match admitted attempts")
            unused_model_calls = reserved_model_calls - reservation.incurred_model_calls
            self._users[(user_key, day)] = self._commit_count(
                self._users.get((user_key, day), UsageCount()),
                input_tokens,
                output_tokens,
                estimated_cost_micros,
                unused_model_calls,
            )
            self._global[day] = self._commit_count(
                self._global.get(day, UsageCount()),
                input_tokens,
                output_tokens,
                estimated_cost_micros,
                unused_model_calls,
            )
            reservation.status = "committed"

    def rollback(self, reservation_id: str, user_key: str, day: date) -> None:
        with self._lock:
            reservation = self._reservations.get(reservation_id)
            if (
                reservation is None
                or (reservation.user_key, reservation.reservation_day)
                != (user_key, day)
                or reservation.status != "reserved"
            ):
                return
            unused_model_calls = (
                reservation.reserved_model_calls - reservation.incurred_model_calls
            )
            self._users[(user_key, day)] = self._rollback_count(
                self._users.get((user_key, day), UsageCount()),
                unused_model_calls,
            )
            self._global[day] = self._rollback_count(
                self._global.get(day, UsageCount()),
                unused_model_calls,
            )
            reservation.status = "rolled_back"

    @staticmethod
    def _commit_count(
        count: UsageCount,
        input_tokens: int,
        output_tokens: int,
        estimated_cost_micros: int,
        unused_model_calls: int,
    ) -> UsageCount:
        if count.pending < unused_model_calls:
            raise RuntimeError("usage reservation is missing")
        return UsageCount(
            count.request_count + 1,
            count.pending - unused_model_calls,
            count.input_tokens + input_tokens,
            count.output_tokens + output_tokens,
            count.model_calls,
            count.estimated_cost_micros + estimated_cost_micros,
        )

    @staticmethod
    def _incur_reserved_call(count: UsageCount) -> UsageCount:
        if count.pending < 1:
            raise RuntimeError("usage reservation is missing")
        return UsageCount(
            count.request_count,
            count.pending - 1,
            count.input_tokens,
            count.output_tokens,
            count.model_calls + 1,
            count.estimated_cost_micros,
        )

    @staticmethod
    def _record_model_call(count: UsageCount) -> UsageCount:
        return UsageCount(
            count.request_count,
            count.pending,
            count.input_tokens,
            count.output_tokens,
            count.model_calls + 1,
            count.estimated_cost_micros,
        )

    @staticmethod
    def _rollback_count(count: UsageCount, reserved_model_calls: int) -> UsageCount:
        if count.pending < reserved_model_calls:
            raise RuntimeError("usage reservation is missing")
        return UsageCount(
            count.request_count,
            count.pending - reserved_model_calls,
            count.input_tokens,
            count.output_tokens,
            count.model_calls,
            count.estimated_cost_micros,
        )


class UsageReservation:
    def __init__(
        self,
        repository: UsageRepository,
        reservation_id: str,
        user_key: str,
        day: date,
        *,
        user_daily_limit: int,
        global_daily_limit: int,
        clock: Callable[[], datetime],
        input_cost_micros_per_million_tokens: int = 0,
        output_cost_micros_per_million_tokens: int = 0,
    ) -> None:
        self._repository = repository
        self.id = reservation_id
        self._user_key = user_key
        self._day = day
        self._user_daily_limit = user_daily_limit
        self._global_daily_limit = global_daily_limit
        self._clock = clock
        self._input_cost_rate = input_cost_micros_per_million_tokens
        self._output_cost_rate = output_cost_micros_per_million_tokens
        self._incurred_model_calls = 0
        self._settled = False
        self._lock = RLock()

    def admit_model_call(self) -> None:
        with self._lock:
            if self._settled:
                raise ProviderUnavailable()
            if self._incurred_model_calls >= MODEL_CALL_SLOTS_PER_REQUEST:
                raise ProviderUnavailable()
            call_day = self._clock().astimezone(UTC).date()
            result = self._repository.admit_model_call(
                self.id,
                self._user_key,
                self._day,
                call_day,
                self._user_daily_limit,
                self._global_daily_limit,
            )
            if not result.admitted:
                if result.failure_reason == "user_limit":
                    raise AppError("AI_DAILY_LIMIT_REACHED", "AI daily limit reached")
                if result.failure_reason == "global_limit":
                    raise AppError(
                        "AI_GLOBAL_DAILY_LIMIT_REACHED",
                        "AI global daily limit reached",
                    )
                raise ProviderUnavailable()
            self._incurred_model_calls += 1

    def estimate_cost_micros(self, input_tokens: int, output_tokens: int) -> int:
        safe_input = max(0, input_tokens)
        safe_output = max(0, output_tokens)
        return (
            safe_input * self._input_cost_rate
            + safe_output * self._output_cost_rate
            + 999_999
        ) // 1_000_000

    def commit(
        self,
        input_tokens: int = 0,
        output_tokens: int = 0,
        model_calls: int = 0,
    ) -> None:
        with self._lock:
            if self._settled:
                return
            safe_input = max(0, input_tokens)
            safe_output = max(0, output_tokens)
            safe_model_calls = max(0, model_calls)
            if safe_model_calls > MODEL_CALL_SLOTS_PER_REQUEST:
                raise ValueError("actual model calls exceed the reservation")
            while self._incurred_model_calls < safe_model_calls:
                self.admit_model_call()
            if self._incurred_model_calls != safe_model_calls:
                raise ValueError("actual model calls do not match admitted attempts")
            estimated_cost_micros = self.estimate_cost_micros(
                safe_input, safe_output
            )
            self._repository.commit(
                self.id,
                self._user_key,
                self._day,
                safe_input,
                safe_output,
                safe_model_calls,
                estimated_cost_micros,
            )
            self._settled = True

    def rollback(self) -> None:
        with self._lock:
            if self._settled:
                return
            self._repository.rollback(self.id, self._user_key, self._day)
            self._settled = True


class UsageGuard:
    def __init__(
        self,
        *,
        repository: UsageRepository,
        user_daily_limit: int,
        global_daily_limit: int,
        enabled: bool,
        provider_configured: bool = True,
        clock: Callable[[], datetime] | None = None,
        input_cost_micros_per_million_tokens: int = 0,
        output_cost_micros_per_million_tokens: int = 0,
    ) -> None:
        self.repository = repository
        self._user_daily_limit = user_daily_limit
        self._global_daily_limit = global_daily_limit
        self._enabled = enabled
        self._provider_configured = provider_configured
        self._clock = clock or (lambda: datetime.now(UTC))
        self._input_cost_rate = input_cost_micros_per_million_tokens
        self._output_cost_rate = output_cost_micros_per_million_tokens

    def reserve(self, user_key: str) -> UsageReservation:
        if not self._enabled:
            raise AppError("AI_DISABLED", "AI is temporarily disabled")
        if not self._provider_configured:
            raise ProviderUnavailable()
        day = self._clock().astimezone(UTC).date()
        result = self.repository.reserve(user_key, day, self._user_daily_limit, self._global_daily_limit)
        if result.reservation_id is None:
            code = "AI_DAILY_LIMIT_REACHED" if result.failure_reason == "user_limit" else "AI_GLOBAL_DAILY_LIMIT_REACHED"
            raise AppError(code, "AI daily limit reached" if code == "AI_DAILY_LIMIT_REACHED" else "AI global daily limit reached")
        return UsageReservation(
            self.repository,
            result.reservation_id,
            user_key,
            day,
            user_daily_limit=self._user_daily_limit,
            global_daily_limit=self._global_daily_limit,
            clock=self._clock,
            input_cost_micros_per_million_tokens=self._input_cost_rate,
            output_cost_micros_per_million_tokens=self._output_cost_rate,
        )
