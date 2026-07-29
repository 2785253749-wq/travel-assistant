from __future__ import annotations

from datetime import UTC, datetime
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.core.errors import AppError
from app.core.usage import InMemoryUsageRepository, ModelGateway, ProviderCircuitBreaker, UsageGuard, classify_provider_error, model_usage_scope


TODAY = datetime(2026, 7, 29, tzinfo=UTC).date()


def make_guard(*, user_limit: int = 5, global_limit: int = 100, enabled: bool = True):
    repository = InMemoryUsageRepository()
    return UsageGuard(
        repository=repository,
        user_daily_limit=user_limit,
        global_daily_limit=global_limit,
        enabled=enabled,
        clock=lambda: datetime(2026, 7, 29, tzinfo=UTC),
    )


def test_user_limit_blocks_a_sixth_reservation_without_changing_pending():
    guard = make_guard()
    guard.repository.set_user_count("user-a", 5, day=TODAY)

    with pytest.raises(AppError, match="daily limit") as error:
        guard.reserve("user-a")

    assert error.value.code == "AI_DAILY_LIMIT_REACHED"
    assert guard.repository.get_daily("user-a", TODAY).pending == 0


def test_rollback_releases_a_reserved_request():
    guard = make_guard()

    reservation = guard.reserve("user-a")
    assert guard.repository.get_daily("user-a", TODAY).pending == 1
    reservation.rollback()

    assert guard.repository.get_daily("user-a", TODAY).pending == 0
    assert guard.repository.get_daily("user-a", TODAY).request_count == 0


def test_commit_converts_pending_to_completed_and_records_real_tokens():
    guard = make_guard()
    reservation = guard.reserve("user-a")

    reservation.commit(input_tokens=13, output_tokens=29)

    count = guard.repository.get_daily("user-a", TODAY)
    assert (count.request_count, count.pending, count.input_tokens, count.output_tokens) == (1, 0, 13, 29)


def test_parallel_reservations_cannot_oversell_a_global_daily_limit():
    guard = make_guard(global_limit=3, user_limit=10)

    def reserve(index: int) -> str:
        try:
            guard.reserve(f"anon:{index}")
            return "allowed"
        except AppError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=12) as pool:
        outcomes = list(pool.map(reserve, range(12)))

    assert outcomes.count("allowed") == 3
    assert outcomes.count("AI_GLOBAL_DAILY_LIMIT_REACHED") == 9
    assert guard.repository.get_global_daily(TODAY).pending == 3


def test_manual_kill_switch_rejects_before_any_reservation():
    guard = make_guard(enabled=False)

    with pytest.raises(AppError) as error:
        guard.reserve("user-a")

    assert error.value.code == "AI_DISABLED"
    assert guard.repository.get_daily("user-a", TODAY).request_count == 0


def test_provider_429_and_5xx_have_stable_non_leaking_classifications():
    class VendorError(Exception):
        def __init__(self, status_code):
            self.status_code = status_code
            super().__init__("raw provider body key=secret")

    assert classify_provider_error(VendorError(429)) == "AI_PROVIDER_RATE_LIMITED"
    assert classify_provider_error(VendorError(503)) == "AI_PROVIDER_UNAVAILABLE"


def test_circuit_breaker_opens_after_consecutive_upstream_failures():
    breaker = ProviderCircuitBreaker(failure_threshold=2)

    breaker.record_failure("AI_PROVIDER_UNAVAILABLE")
    assert breaker.allow()
    breaker.record_failure("AI_PROVIDER_UNAVAILABLE")

    assert not breaker.allow()


def test_gateway_never_invokes_model_after_circuit_opens_and_collects_tokens():
    class Model:
        def __init__(self): self.calls = 0
        def invoke(self, _messages):
            self.calls += 1
            if self.calls < 3:
                error = RuntimeError("upstream")
                error.status_code = 503
                raise error
            return type("Response", (), {"content": "ok", "usage_metadata": {"input_tokens": 7, "output_tokens": 11}})()

    model = Model()
    gateway = ModelGateway(lambda: model, ProviderCircuitBreaker(failure_threshold=2))
    with model_usage_scope() as usage:
        for _ in range(2):
            with pytest.raises(Exception): gateway.invoke([])
        with pytest.raises(Exception) as error: gateway.invoke([])

    assert getattr(error.value, "code") == "AI_CIRCUIT_OPEN"
    assert model.calls == 2
    assert usage.calls == 0
