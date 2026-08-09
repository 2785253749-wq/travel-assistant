"""Server-owned AI model-call accounting and circuit-breaker controls.

The in-memory repository is deliberately lock-protected for local/test use.
Production deployments can use the accompanying SQL RPC migration so each
reservation is made in one database transaction across web workers.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from threading import RLock
from typing import Any, Callable, Protocol
from contextlib import contextmanager
from contextvars import ContextVar
from uuid import uuid4

from app.core.errors import AppError


MODEL_CALL_SLOTS_PER_REQUEST = 2


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

    def record_attempt(self) -> None:
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
def model_usage_scope():
    usage = ModelUsage()
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
        except Exception as exc:
            code = classify_provider_error(exc)
            self._breaker.record_failure(code)
            raise ProviderUnavailable("AI_RATE_LIMITED" if code == "AI_PROVIDER_RATE_LIMITED" else "AI_UNAVAILABLE") from None
        self._breaker.record_success()
        if collector is not None:
            collector.record_tokens(response)
        return response


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


class UsageRepository(Protocol):
    def get_daily(self, user_key: str, day: date) -> UsageCount: ...
    def get_global_daily(self, day: date) -> UsageCount: ...
    def reserve(self, user_key: str, day: date, user_limit: int, global_limit: int) -> ReserveResult: ...
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


class InMemoryUsageRepository:
    """Atomic reference implementation used without any network dependency."""

    def __init__(self, *, clock: Callable[[], datetime] | None = None) -> None:
        self._lock = RLock()
        self._users: dict[tuple[str, date], UsageCount] = {}
        self._global: dict[date, UsageCount] = {}
        self._reservations: dict[str, tuple[str, date, datetime, str, int]] = {}
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
            self._reservations[reservation_id] = (
                user_key,
                day,
                now + timedelta(minutes=5),
                "reserved",
                MODEL_CALL_SLOTS_PER_REQUEST,
            )
            return ReserveResult(reservation_id, None)

    def _cleanup_expired(self, day: date, now: datetime) -> None:
        for reservation_id, (
            subject,
            reserved_day,
            expires_at,
            status,
            reserved_model_calls,
        ) in tuple(self._reservations.items()):
            if reserved_day == day and status == "reserved" and expires_at <= now:
                # An expired reservation may already have incurred a provider call.
                # Keep its worst-case slots pending until a late settlement arrives;
                # releasing them would allow the daily ceiling to be oversold.
                self._reservations[reservation_id] = (
                    subject,
                    reserved_day,
                    expires_at,
                    "expired",
                    reserved_model_calls,
                )

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
                or reservation[:2] != (user_key, day)
                or reservation[3] not in {"reserved", "expired"}
            ):
                return
            reserved_model_calls = reservation[4]
            if model_calls < 0 or model_calls > reserved_model_calls:
                raise ValueError("actual model calls exceed the reservation")
            self._users[(user_key, day)] = self._commit_count(
                self._users.get((user_key, day), UsageCount()),
                input_tokens,
                output_tokens,
                model_calls,
                estimated_cost_micros,
                reserved_model_calls,
            )
            self._global[day] = self._commit_count(
                self._global.get(day, UsageCount()),
                input_tokens,
                output_tokens,
                model_calls,
                estimated_cost_micros,
                reserved_model_calls,
            )
            self._reservations[reservation_id] = (
                *reservation[:3],
                "committed",
                reserved_model_calls,
            )

    def rollback(self, reservation_id: str, user_key: str, day: date) -> None:
        with self._lock:
            reservation = self._reservations.get(reservation_id)
            if reservation is None or reservation[:2] != (user_key, day) or reservation[3] != "reserved": return
            reserved_model_calls = reservation[4]
            self._users[(user_key, day)] = self._rollback_count(
                self._users.get((user_key, day), UsageCount()),
                reserved_model_calls,
            )
            self._global[day] = self._rollback_count(
                self._global.get(day, UsageCount()),
                reserved_model_calls,
            )
            self._reservations[reservation_id] = (
                *reservation[:3],
                "rolled_back",
                reserved_model_calls,
            )

    @staticmethod
    def _commit_count(
        count: UsageCount,
        input_tokens: int,
        output_tokens: int,
        model_calls: int,
        estimated_cost_micros: int,
        reserved_model_calls: int,
    ) -> UsageCount:
        if count.pending < reserved_model_calls:
            raise RuntimeError("usage reservation is missing")
        return UsageCount(
            count.request_count + 1,
            count.pending - reserved_model_calls,
            count.input_tokens + input_tokens,
            count.output_tokens + output_tokens,
            count.model_calls + model_calls,
            count.estimated_cost_micros + estimated_cost_micros,
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
        input_cost_micros_per_million_tokens: int = 0,
        output_cost_micros_per_million_tokens: int = 0,
    ) -> None:
        self._repository = repository
        self.id = reservation_id
        self._user_key = user_key
        self._day = day
        self._input_cost_rate = input_cost_micros_per_million_tokens
        self._output_cost_rate = output_cost_micros_per_million_tokens
        self._settled = False

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
        if self._settled:
            return
        safe_input = max(0, input_tokens)
        safe_output = max(0, output_tokens)
        estimated_cost_micros = self.estimate_cost_micros(safe_input, safe_output)
        self._repository.commit(
            self.id,
            self._user_key,
            self._day,
            safe_input,
            safe_output,
            max(0, model_calls),
            estimated_cost_micros,
        )
        self._settled = True

    def rollback(self) -> None:
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
            input_cost_micros_per_million_tokens=self._input_cost_rate,
            output_cost_micros_per_million_tokens=self._output_cost_rate,
        )
