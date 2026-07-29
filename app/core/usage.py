"""Server-owned AI request accounting and circuit-breaker controls.

The in-memory repository is deliberately lock-protected for local/test use.
Production deployments can use the accompanying SQL RPC migration so each
reservation is made in one database transaction across web workers.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from threading import Lock, RLock
from typing import Any, Callable, Protocol
from contextlib import contextmanager
from contextvars import ContextVar
from uuid import uuid4

from app.core.config import get_settings
from app.core.errors import AppError


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

    def record(self, response: object) -> None:
        metadata = getattr(response, "usage_metadata", None) or getattr(response, "usage", None) or {}
        self.calls += 1
        self.input_tokens += max(0, int(metadata.get("input_tokens", metadata.get("prompt_tokens", 0)) or 0))
        self.output_tokens += max(0, int(metadata.get("output_tokens", metadata.get("completion_tokens", 0)) or 0))


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
            response = client.invoke(messages)
        except ProviderUnavailable:
            raise
        except Exception as exc:
            code = classify_provider_error(exc)
            self._breaker.record_failure(code)
            raise ProviderUnavailable("AI_RATE_LIMITED" if code == "AI_PROVIDER_RATE_LIMITED" else "AI_UNAVAILABLE") from None
        self._breaker.record_success()
        collector = _model_usage.get()
        if collector is not None:
            collector.record(response)
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

    @property
    def reserved_count(self) -> int:
        return self.request_count + self.pending


class UsageRepository(Protocol):
    def get_daily(self, user_key: str, day: date) -> UsageCount: ...
    def get_global_daily(self, day: date) -> UsageCount: ...
    def reserve(self, user_key: str, day: date, user_limit: int, global_limit: int) -> str | None: ...
    def commit(self, reservation_id: str, user_key: str, day: date, input_tokens: int, output_tokens: int) -> None: ...
    def rollback(self, reservation_id: str, user_key: str, day: date) -> None: ...


class InMemoryUsageRepository:
    """Atomic reference implementation used without any network dependency."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._users: dict[tuple[str, date], UsageCount] = {}
        self._global: dict[date, UsageCount] = {}
        self._reservations: dict[str, tuple[str, date, datetime, str]] = {}

    def get_daily(self, user_key: str, day: date) -> UsageCount:
        with self._lock:
            return self._users.get((user_key, day), UsageCount())

    def get_global_daily(self, day: date) -> UsageCount:
        with self._lock:
            return self._global.get(day, UsageCount())

    def set_user_count(self, user_key: str, request_count: int, *, day: date) -> None:
        with self._lock:
            current = self._users.get((user_key, day), UsageCount())
            self._users[(user_key, day)] = UsageCount(request_count, current.pending, current.input_tokens, current.output_tokens)
            global_count = self._global.get(day, UsageCount())
            self._global[day] = UsageCount(request_count, global_count.pending, global_count.input_tokens, global_count.output_tokens)

    def reserve(self, user_key: str, day: date, user_limit: int, global_limit: int) -> str | None:
        with self._lock:
            user = self._users.get((user_key, day), UsageCount())
            global_count = self._global.get(day, UsageCount())
            if user.reserved_count >= user_limit:
                return None
            if global_count.reserved_count >= global_limit:
                return None
            self._users[(user_key, day)] = UsageCount(user.request_count, user.pending + 1, user.input_tokens, user.output_tokens)
            self._global[day] = UsageCount(global_count.request_count, global_count.pending + 1, global_count.input_tokens, global_count.output_tokens)
            reservation_id = str(uuid4())
            self._reservations[reservation_id] = (user_key, day, datetime.now(UTC) + timedelta(minutes=5), "reserved")
            return reservation_id

    def commit(self, reservation_id: str, user_key: str, day: date, input_tokens: int, output_tokens: int) -> None:
        with self._lock:
            reservation = self._reservations.get(reservation_id)
            if reservation is None or reservation[:2] != (user_key, day) or reservation[3] == "committed": return
            if reservation[2] < datetime.now(UTC):
                self.rollback(reservation_id, user_key, day); return
            self._users[(user_key, day)] = self._commit_count(self._users.get((user_key, day), UsageCount()), input_tokens, output_tokens)
            self._global[day] = self._commit_count(self._global.get(day, UsageCount()), input_tokens, output_tokens)
            self._reservations[reservation_id] = (*reservation[:3], "committed")

    def rollback(self, reservation_id: str, user_key: str, day: date) -> None:
        with self._lock:
            reservation = self._reservations.get(reservation_id)
            if reservation is None or reservation[:2] != (user_key, day) or reservation[3] != "reserved": return
            self._users[(user_key, day)] = self._rollback_count(self._users.get((user_key, day), UsageCount()))
            self._global[day] = self._rollback_count(self._global.get(day, UsageCount()))
            self._reservations[reservation_id] = (*reservation[:3], "rolled_back")

    @staticmethod
    def _commit_count(count: UsageCount, input_tokens: int, output_tokens: int) -> UsageCount:
        if count.pending <= 0:
            raise RuntimeError("usage reservation is missing")
        return UsageCount(count.request_count + 1, count.pending - 1, count.input_tokens + input_tokens, count.output_tokens + output_tokens)

    @staticmethod
    def _rollback_count(count: UsageCount) -> UsageCount:
        if count.pending <= 0:
            raise RuntimeError("usage reservation is missing")
        return UsageCount(count.request_count, count.pending - 1, count.input_tokens, count.output_tokens)


class SupabaseUsageRepository:
    """Server-only service-role adapter for the atomic usage RPCs."""

    def __init__(self, client: object) -> None:
        self._client = client

    @classmethod
    def from_settings(cls) -> "SupabaseUsageRepository":
        settings = get_settings()
        if settings.supabase_url is None or settings.supabase_service_key is None:
            raise RuntimeError("server-side usage storage is not configured")
        from supabase import create_client
        return cls(create_client(str(settings.supabase_url), settings.supabase_service_key.get_secret_value()))

    @staticmethod
    def _data(response: object) -> object:
        return getattr(response, "data", response)

    def get_daily(self, user_key: str, day: date) -> UsageCount:
        # Reads are only used to classify a failed atomic reservation.
        response = self._client.rpc("get_ai_usage", {"p_subject_key": user_key, "p_usage_date": day.isoformat()}).execute()
        row = self._data(response) or {}
        if isinstance(row, list): row = row[0] if row else {}
        return UsageCount(**{key: int(row.get(key, 0)) for key in UsageCount.__dataclass_fields__})

    def get_global_daily(self, day: date) -> UsageCount:
        response = self._client.rpc("get_ai_global_usage", {"p_usage_date": day.isoformat()}).execute()
        row = self._data(response) or {}
        if isinstance(row, list): row = row[0] if row else {}
        return UsageCount(**{key: int(row.get(key, 0)) for key in UsageCount.__dataclass_fields__})

    def reserve(self, user_key: str, day: date, user_limit: int, global_limit: int) -> str | None:
        result = self._data(self._client.rpc("reserve_ai_usage", {"p_subject_key": user_key, "p_usage_date": day.isoformat(), "p_user_limit": user_limit, "p_global_limit": global_limit}).execute())
        return result if isinstance(result, str) and result not in {"user_limit", "global_limit"} else None

    def commit(self, reservation_id: str, user_key: str, day: date, input_tokens: int, output_tokens: int) -> None:
        self._client.rpc("commit_ai_usage", {"p_reservation_id": reservation_id, "p_subject_key": user_key, "p_usage_date": day.isoformat(), "p_input_tokens": input_tokens, "p_output_tokens": output_tokens}).execute()

    def rollback(self, reservation_id: str, user_key: str, day: date) -> None:
        self._client.rpc("rollback_ai_usage", {"p_reservation_id": reservation_id, "p_subject_key": user_key, "p_usage_date": day.isoformat()}).execute()


class UsageReservation:
    def __init__(self, repository: UsageRepository, reservation_id: str, user_key: str, day: date) -> None:
        self._repository = repository
        self.id = reservation_id
        self._user_key = user_key
        self._day = day
        self._settled = False

    def commit(self, input_tokens: int = 0, output_tokens: int = 0) -> None:
        if self._settled:
            return
        self._repository.commit(self.id, self._user_key, self._day, max(0, input_tokens), max(0, output_tokens))
        self._settled = True

    def rollback(self) -> None:
        if self._settled:
            return
        self._repository.rollback(self.id, self._user_key, self._day)
        self._settled = True


class UsageGuard:
    def __init__(self, *, repository: UsageRepository, user_daily_limit: int, global_daily_limit: int, enabled: bool, provider_configured: bool = True, clock: Callable[[], datetime] | None = None) -> None:
        self.repository = repository
        self._user_daily_limit = user_daily_limit
        self._global_daily_limit = global_daily_limit
        self._enabled = enabled
        self._provider_configured = provider_configured
        self._clock = clock or (lambda: datetime.now(UTC))

    def reserve(self, user_key: str) -> UsageReservation:
        if not self._enabled:
            raise AppError("AI_DISABLED", "AI is temporarily disabled")
        if not self._provider_configured:
            raise ProviderUnavailable()
        day = self._clock().astimezone(UTC).date()
        user = self.repository.get_daily(user_key, day)
        if user.reserved_count >= self._user_daily_limit:
            raise AppError("AI_DAILY_LIMIT_REACHED", "AI daily limit reached")
        global_count = self.repository.get_global_daily(day)
        if global_count.reserved_count >= self._global_daily_limit:
            raise AppError("AI_GLOBAL_DAILY_LIMIT_REACHED", "AI global daily limit reached")
        reservation_id = self.repository.reserve(user_key, day, self._user_daily_limit, self._global_daily_limit)
        if not reservation_id:
            # A concurrent transaction won the race. Re-read only stable counts.
            if self.repository.get_daily(user_key, day).reserved_count >= self._user_daily_limit:
                raise AppError("AI_DAILY_LIMIT_REACHED", "AI daily limit reached")
            raise AppError("AI_GLOBAL_DAILY_LIMIT_REACHED", "AI global daily limit reached")
        return UsageReservation(self.repository, reservation_id, user_key, day)


_repository = InMemoryUsageRepository()


def get_usage_guard() -> UsageGuard:
    settings = get_settings()
    configured = settings.app_env != "production" or (
        settings.deepseek_api_key is not None and bool(settings.deepseek_api_key.get_secret_value().strip())
    )
    repository: UsageRepository = _repository if settings.app_env != "production" else SupabaseUsageRepository.from_settings()
    return UsageGuard(
        repository=repository,
        user_daily_limit=settings.ai_user_daily_limit,
        global_daily_limit=settings.ai_global_daily_limit,
        enabled=settings.ai_enabled,
        provider_configured=configured,
    )
