from __future__ import annotations

from datetime import UTC, datetime, timedelta
import logging
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier, Lock
from types import SimpleNamespace
import re
import sys

import pytest

from app.core.errors import AppError
from app.core.config import get_settings
from app.composition import get_usage_guard
from app.core.usage import CallAdmissionResult, InMemoryUsageRepository, ModelGateway, ProviderCircuitBreaker, ProviderUnavailable, ReserveResult, UsageGuard, classify_provider_error, model_usage_scope
from app.infrastructure.usage import SupabaseUsageRepository


TODAY = datetime(2026, 7, 29, tzinfo=UTC).date()
RESERVATION_ID = "11111111-1111-4111-8111-111111111111"
MIGRATIONS = Path(__file__).parents[2] / "supabase" / "migrations"
RESERVE_SIGNATURE = ("text", "date", "integer", "integer")
COMMIT_SIGNATURE = ("uuid", "text", "date", "integer", "integer")
COST_COMMIT_SIGNATURE = (
    "uuid",
    "text",
    "date",
    "integer",
    "integer",
    "integer",
    "bigint",
)
ADMIT_CALL_SIGNATURE = (
    "uuid",
    "text",
    "date",
    "date",
    "integer",
    "integer",
)
ROLLBACK_SIGNATURE = ("uuid", "text", "date")


def test_core_usage_has_no_config_or_supabase_adapter_dependency():
    source = Path("app/core/usage.py").read_text(encoding="utf-8")

    assert "app.core.config" not in source
    assert "class SupabaseUsageRepository" not in source
    assert "from supabase" not in source


def _parse_usage_functions(sql: str, migration_name: str):
    contracts = {}
    pattern = re.compile(
        r"create(?:\s+or\s+replace)?\s+function\s+public\."
        r"(?P<name>reserve_ai_usage|admit_ai_usage_call|commit_ai_usage|rollback_ai_usage)\s*"
        r"\((?P<parameters>.*?)\)\s*returns\s+(?P<return_type>\w+)\s+"
        r"language\s+\w+.*?\bas\s+\$\$(?P<body>.*?)\$\$;",
        re.IGNORECASE | re.DOTALL,
    )
    for match in pattern.finditer(sql):
        parameters = []
        for declaration in match.group("parameters").split(","):
            name, sql_type = declaration.strip().split(None, 1)
            parameters.append((name.lower(), sql_type.lower()))
        key = (match.group("name").lower(), tuple(sql_type for _, sql_type in parameters))
        contracts[key] = {
            "parameters": parameters,
            "return_type": match.group("return_type").lower(),
            "body": match.group("body"),
            "migration_name": migration_name,
            "migration_sql": sql,
        }
    return contracts


def _final_usage_contracts():
    contracts = {}
    for migration in sorted(MIGRATIONS.glob("*.sql")):
        sql = migration.read_text(encoding="utf-8")
        contracts.update(_parse_usage_functions(sql, migration.name))
    return contracts


def _normalized(sql: str) -> str:
    return " ".join(sql.lower().split())


def _assert_service_role_grant(contract, function_name: str, parameter_types: tuple[str, ...]):
    signature = f"public.{function_name}({', '.join(parameter_types)})"
    sql = _normalized(contract["migration_sql"])
    assert f"revoke all on function {signature} from public;" in sql
    assert f"grant execute on function {signature} to service_role;" in sql


class MutableClock:
    def __init__(self, current: datetime):
        self.current = current

    def __call__(self) -> datetime:
        return self.current

    def advance(self, delta: timedelta) -> None:
        self.current += delta


class CountingModel:
    def __init__(self, lock: Lock | None = None):
        self.calls = 0
        self._lock = lock

    def invoke(self, _messages):
        if self._lock is None:
            self.calls += 1
        else:
            with self._lock:
                self.calls += 1
        return SimpleNamespace(usage_metadata={})


def make_guard(
    *,
    user_limit: int = 5,
    global_limit: int = 100,
    enabled: bool = True,
    clock=None,
    repository=None,
):
    clock = clock or (lambda: datetime(2026, 7, 29, tzinfo=UTC))
    repository = repository or InMemoryUsageRepository(clock=clock)
    return UsageGuard(
        repository=repository,
        user_daily_limit=user_limit,
        global_daily_limit=global_limit,
        enabled=enabled,
        clock=clock,
    )


def make_cross_midnight_guard():
    clock = MutableClock(datetime(2026, 8, 9, 23, 59, 59, tzinfo=UTC))
    guard = make_guard(user_limit=2, global_limit=2, clock=clock)
    reservation = guard.reserve("user-a")
    clock.advance(timedelta(seconds=2))
    return clock, guard, guard.repository, reservation


def invoke(reservation, *, calls: int = 1, model=None):
    model = model or CountingModel()
    with model_usage_scope(reservation) as usage:
        gateway = ModelGateway(lambda: model)
        for _ in range(calls):
            gateway.invoke([])
    return model, usage


def fill_day(repository, user_key: str, day, limit: int = 2) -> None:
    reservation = repository.reserve(user_key, day, limit, limit)
    for _ in range(2):
        assert repository.admit_model_call(
            reservation.reservation_id, user_key, day, day, limit, limit
        ).admitted
    repository.commit(reservation.reservation_id, user_key, day, 0, 0, 2, 0)


def test_user_limit_counts_model_calls_and_blocks_when_two_slots_would_exceed_it():
    guard = make_guard()

    for _ in range(2):
        reservation = guard.reserve("user-a")
        reservation.commit(model_calls=2)

    with pytest.raises(AppError, match="daily limit") as error:
        guard.reserve("user-a")

    assert error.value.code == "AI_DAILY_LIMIT_REACHED"
    count = guard.repository.get_daily("user-a", TODAY)
    assert (count.request_count, count.model_calls, count.pending) == (2, 4, 0)


def test_rollback_releases_two_reserved_model_call_slots():
    guard = make_guard()

    reservation = guard.reserve("user-a")
    assert guard.repository.get_daily("user-a", TODAY).pending == 2
    reservation.rollback()

    assert guard.repository.get_daily("user-a", TODAY).pending == 0
    assert guard.repository.get_daily("user-a", TODAY).request_count == 0


def test_commit_converts_pending_to_completed_and_records_real_tokens():
    guard = make_guard()
    reservation = guard.reserve("user-a")

    reservation.commit(input_tokens=13, output_tokens=29, model_calls=2)

    count = guard.repository.get_daily("user-a", TODAY)
    assert (
        count.request_count,
        count.pending,
        count.input_tokens,
        count.output_tokens,
        count.model_calls,
    ) == (1, 0, 13, 29, 2)


def test_commit_releases_the_unused_model_call_slot():
    guard = make_guard()

    first = guard.reserve("user-a")
    first.commit(model_calls=1)
    second = guard.reserve("user-a")
    third = guard.reserve("user-a")

    with pytest.raises(AppError) as error:
        guard.reserve("user-a")

    count = guard.repository.get_daily("user-a", TODAY)
    assert error.value.code == "AI_DAILY_LIMIT_REACHED"
    assert (count.request_count, count.model_calls, count.pending) == (1, 1, 4)
    second.rollback()
    third.rollback()


def test_commit_rejects_more_actual_calls_than_the_two_reserved_slots():
    guard = make_guard()
    reservation = guard.reserve("user-a")

    with pytest.raises(ValueError, match="exceed the reservation"):
        reservation.commit(model_calls=3)

    count = guard.repository.get_daily("user-a", TODAY)
    assert (count.request_count, count.model_calls, count.pending) == (0, 0, 2)
    reservation.rollback()


def test_parallel_reservations_cannot_oversell_a_global_daily_limit():
    guard = make_guard(global_limit=100, user_limit=100)

    def reserve(index: int) -> str:
        try:
            guard.reserve(f"anon:{index}")
            return "allowed"
        except AppError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=60) as pool:
        outcomes = list(pool.map(reserve, range(60)))

    assert outcomes.count("allowed") == 50
    assert outcomes.count("AI_GLOBAL_DAILY_LIMIT_REACHED") == 10
    global_count = guard.repository.get_global_daily(TODAY)
    assert global_count.model_calls + global_count.pending == 100


def test_cross_midnight_model_invokes_consume_the_invoke_days_global_quota():
    clock, guard, repository, reservation = make_cross_midnight_guard()
    model, usage = invoke(reservation, calls=2)
    reservation.commit(model_calls=usage.calls)

    assert model.calls == 2
    assert repository.get_global_daily(clock.current.date() - timedelta(days=1)).model_calls == 0
    assert repository.get_global_daily(clock.current.date()).model_calls == 2
    with pytest.raises(AppError) as error:
        guard.reserve("user-b")
    assert error.value.code == "AI_GLOBAL_DAILY_LIMIT_REACHED"


def test_full_invoke_day_rejects_before_the_provider_call():
    clock, _, repository, cross_midnight = make_cross_midnight_guard()
    fill_day(repository, "user-b", clock.current.date())
    model = CountingModel()
    with pytest.raises(AppError) as error:
        with model_usage_scope(cross_midnight):
            ModelGateway(lambda: model).invoke([])

    assert error.value.code == "AI_GLOBAL_DAILY_LIMIT_REACHED"
    assert model.calls == 0


def test_parallel_cross_midnight_attempts_and_new_day_reserve_do_not_oversell():
    clock, guard, repository, cross_midnight = make_cross_midnight_guard()
    start = Barrier(3)
    model = CountingModel(Lock())
    gateway = ModelGateway(lambda: model)

    def invoke() -> str:
        start.wait()
        try:
            with model_usage_scope(cross_midnight):
                gateway.invoke([])
            return "called"
        except AppError as error:
            return error.code

    def reserve_new_day() -> str:
        start.wait()
        try:
            guard.reserve("user-b")
            return "reserved"
        except AppError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = [pool.submit(invoke), pool.submit(invoke), pool.submit(reserve_new_day)]
        outcomes = [future.result() for future in futures]

    global_count = repository.get_global_daily(clock.current.date())
    assert global_count.reserved_model_calls == 2
    assert global_count.model_calls == model.calls
    assert outcomes.count("called") == model.calls


def test_settlement_failure_keeps_incurred_and_unused_slots_fail_closed():
    clock = MutableClock(datetime(2026, 8, 9, 12, tzinfo=UTC))
    base_repository = InMemoryUsageRepository(clock=clock)

    class Repository:
        fail_commit = True

        def __getattr__(self, name):
            return getattr(base_repository, name)

        def commit(self, *args):
            if self.fail_commit:
                raise ProviderUnavailable()
            return base_repository.commit(*args)

    repository = Repository()
    guard = make_guard(
        repository=repository, user_limit=2, global_limit=2, clock=clock
    )
    reservation = guard.reserve("user-a")
    _, usage = invoke(reservation)

    with pytest.raises(ProviderUnavailable):
        reservation.commit(model_calls=usage.calls)
    count = base_repository.get_global_daily(clock.current.date())
    assert (count.model_calls, count.pending) == (1, 1)
    with pytest.raises(AppError) as error:
        guard.reserve("user-b")
    assert error.value.code == "AI_GLOBAL_DAILY_LIMIT_REACHED"

    repository.fail_commit = False
    reservation.commit(model_calls=usage.calls)
    count = base_repository.get_global_daily(clock.current.date())
    assert (count.model_calls, count.pending) == (1, 0)


def test_expired_reservation_rejects_before_provider_and_keeps_slots():
    clock = MutableClock(datetime(2026, 8, 9, 12, tzinfo=UTC))
    guard = make_guard(user_limit=2, global_limit=2, clock=clock)
    repository = guard.repository
    reservation = guard.reserve("user-a")
    clock.advance(timedelta(minutes=6))
    model = CountingModel()
    with pytest.raises(ProviderUnavailable):
        with model_usage_scope(reservation):
            ModelGateway(lambda: model).invoke([])

    count = repository.get_global_daily(clock.current.date())
    assert model.calls == 0
    assert (count.model_calls, count.pending) == (0, 2)


def test_call_admitted_before_expiry_can_be_settled_after_expiry():
    clock = MutableClock(datetime(2026, 8, 9, 12, tzinfo=UTC))
    guard = make_guard(user_limit=2, global_limit=2, clock=clock)
    repository = guard.repository
    reservation = guard.reserve("user-a")
    _, usage = invoke(reservation)
    clock.advance(timedelta(minutes=6))
    with pytest.raises(AppError):
        guard.reserve("user-b")

    reservation.commit(model_calls=usage.calls)

    count = repository.get_global_daily(clock.current.date())
    assert (count.request_count, count.model_calls, count.pending) == (1, 1, 0)


def test_expired_unsettled_reservation_keeps_global_slots_fail_closed():
    now = datetime(2026, 7, 29, tzinfo=UTC)
    clock = lambda: now
    repository = InMemoryUsageRepository(clock=clock)
    guard = UsageGuard(
        repository=repository,
        user_daily_limit=2,
        global_daily_limit=2,
        enabled=True,
        clock=clock,
    )

    guard.reserve("user-a")
    now += timedelta(minutes=6)

    with pytest.raises(AppError) as error:
        guard.reserve("user-b")

    assert error.value.code == "AI_GLOBAL_DAILY_LIMIT_REACHED"
    global_count = repository.get_global_daily(TODAY)
    assert (global_count.model_calls, global_count.pending) == (0, 2)


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


def test_gateway_logs_a_safe_provider_status_without_the_vendor_body(caplog):
    class VendorError(Exception):
        status_code = 401

        def __init__(self):
            super().__init__("raw vendor body contains api_key=do-not-log")

    class Model:
        def invoke(self, _messages):
            raise VendorError()

    with caplog.at_level("WARNING", logger="app.model"):
        with pytest.raises(ProviderUnavailable):
            ModelGateway(lambda: Model()).invoke([])

    record = next(record for record in caplog.records if record.message == "model_provider_failure")
    assert record.provider == "deepseek"
    assert record.provider_status == 401
    assert "do-not-log" not in caplog.text


def test_gateway_retries_one_transient_503_within_the_existing_usage_reservation():
    class VendorError(Exception):
        status_code = 503

    class Model:
        def __init__(self):
            self.calls = 0

        def invoke(self, _messages):
            self.calls += 1
            if self.calls == 1:
                raise VendorError()
            return SimpleNamespace(
                content="ok",
                usage_metadata={"input_tokens": 2, "output_tokens": 3},
            )

    model = Model()
    repository = InMemoryUsageRepository()
    guard = UsageGuard(
        repository=repository,
        user_daily_limit=10,
        global_daily_limit=10,
        enabled=True,
        provider_configured=True,
    )
    reservation = guard.reserve("retry-user")

    with model_usage_scope(reservation) as usage:
        response = ModelGateway(lambda: model).invoke([])
        reservation.commit(usage.input_tokens, usage.output_tokens, usage.calls)

    assert response.content == "ok"
    assert model.calls == 2
    assert usage.calls == 2
    daily = repository.get_daily("retry-user", datetime.now(UTC).date())
    assert daily.model_calls == 2
    assert daily.pending == 0


def test_gateway_retries_one_timeout_then_succeeds(monkeypatch):
    monkeypatch.setattr("app.core.usage.sleep", lambda _seconds: None)

    class Model:
        def __init__(self):
            self.calls = 0

        def invoke(self, _messages):
            self.calls += 1
            if self.calls == 1:
                raise TimeoutError("transient")
            return SimpleNamespace(content="ok", usage_metadata={})

    model = Model()
    response = ModelGateway(lambda: model).invoke([])

    assert response.content == "ok"
    assert model.calls == 2


def test_gateway_logs_each_retry_attempt_without_model_payload(caplog, monkeypatch):
    monkeypatch.setattr("app.core.usage.sleep", lambda _seconds: None)

    class VendorError(Exception):
        status_code = 503

    class Model:
        calls = 0

        def invoke(self, _messages):
            self.calls += 1
            if self.calls == 1:
                raise VendorError("provider body api_key=do-not-log")
            return SimpleNamespace(content="ok", usage_metadata={})

    model = Model()
    with caplog.at_level("INFO", logger="app.model"):
        ModelGateway(lambda: model).invoke([])

    messages = [record.message for record in caplog.records]
    assert "model attempt=1 start" in messages
    assert "model attempt=1 transient_failure" in messages
    assert "model attempt=2 start" in messages
    assert "model attempt=2 success" in messages
    assert "do-not-log" not in caplog.text


def test_gateway_stops_after_two_transient_failures(monkeypatch):
    monkeypatch.setattr("app.core.usage.sleep", lambda _seconds: None)

    class VendorError(Exception):
        status_code = 503

    class Model:
        def __init__(self):
            self.calls = 0

        def invoke(self, _messages):
            self.calls += 1
            raise VendorError()

    model = Model()
    with pytest.raises(ProviderUnavailable) as error:
        ModelGateway(lambda: model).invoke([])

    assert error.value.code == "AI_UNAVAILABLE"
    assert model.calls == 2


@pytest.mark.parametrize("status_code", [400, 401, 403, 404])
def test_gateway_does_not_retry_authentication_or_other_client_errors(status_code):
    class VendorError(Exception):
        pass

    class Model:
        calls = 0

        def invoke(self, _messages):
            self.calls += 1
            error = VendorError()
            error.status_code = status_code
            raise error

    model = Model()
    with pytest.raises(ProviderUnavailable) as error:
        ModelGateway(lambda: model).invoke([])
    assert error.value.code == "AI_UNAVAILABLE"
    assert model.calls == 1


def test_gateway_does_not_retry_structured_output_setup_validation_error():
    factory_calls = []

    class Model:
        def with_structured_output(self, _schema, method):
            assert method == "json_mode"
            raise ValueError("invalid structured output schema")

    def factory():
        factory_calls.append(1)
        return Model()

    with pytest.raises(ProviderUnavailable) as error:
        ModelGateway(factory).invoke([], structured=object())

    assert error.value.code == "AI_UNAVAILABLE"
    assert factory_calls == [1]


def test_circuit_breaker_opens_after_consecutive_upstream_failures():
    breaker = ProviderCircuitBreaker(failure_threshold=2)

    breaker.record_failure("AI_PROVIDER_UNAVAILABLE")
    assert breaker.allow()
    breaker.record_failure("AI_PROVIDER_UNAVAILABLE")

    assert not breaker.allow()


def test_gateway_never_invokes_model_after_circuit_opens_and_counts_failed_attempts():
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
    assert usage.calls == 2


def test_usage_commit_records_configured_cost_estimate_in_micro_cny():
    repository = InMemoryUsageRepository()
    guard = UsageGuard(
        repository=repository,
        user_daily_limit=5,
        global_daily_limit=100,
        enabled=True,
        clock=lambda: datetime(2026, 7, 29, tzinfo=UTC),
        input_cost_micros_per_million_tokens=2_000_000,
        output_cost_micros_per_million_tokens=8_000_000,
    )

    reservation = guard.reserve("user-a")
    reservation.commit(input_tokens=250_000, output_tokens=125_000, model_calls=2)

    count = repository.get_daily("user-a", TODAY)
    assert count.model_calls == 2
    assert count.estimated_cost_micros == 1_500_000


def test_002_keeps_its_published_text_reserve_contract():
    migration = MIGRATIONS / "002_ai_usage_reservations.sql"
    contracts = _parse_usage_functions(migration.read_text(encoding="utf-8"), migration.name)
    reserve = contracts[("reserve_ai_usage", RESERVE_SIGNATURE)]

    assert reserve["parameters"] == [
        ("p_subject_key", "text"),
        ("p_usage_date", "date"),
        ("p_user_limit", "integer"),
        ("p_global_limit", "integer"),
    ]
    assert reserve["return_type"] == "text"
    assert "jsonb_build_object" not in reserve["body"].lower()
    assert "return 'user_limit'" in reserve["body"].lower()
    assert "return 'global_limit'" in reserve["body"].lower()
    assert "return new_reservation::text" in reserve["body"].lower()


def test_003_drops_and_recreates_reserve_with_the_final_jsonb_contract():
    migration = MIGRATIONS / "003_ai_usage_reservation_protocol.sql"
    sql = migration.read_text(encoding="utf-8")
    normalized = _normalized(sql)
    drop_statement = (
        "drop function if exists public.reserve_ai_usage"
        "(text, date, integer, integer);"
    )
    create_statement = "create function public.reserve_ai_usage("
    revoke_statement = (
        "revoke all on function public.reserve_ai_usage"
        "(text, date, integer, integer) from public;"
    )
    grant_statement = (
        "grant execute on function public.reserve_ai_usage"
        "(text, date, integer, integer) to service_role;"
    )

    assert drop_statement in normalized
    assert create_statement in normalized
    assert revoke_statement in normalized
    assert grant_statement in normalized
    assert (
        normalized.index(drop_statement)
        < normalized.index(create_statement)
        < normalized.index(revoke_statement)
        < normalized.index(grant_statement)
    )

    contracts = _parse_usage_functions(sql, migration.name)
    reserve = contracts[("reserve_ai_usage", RESERVE_SIGNATURE)]
    assert reserve["parameters"] == [
        ("p_subject_key", "text"),
        ("p_usage_date", "date"),
        ("p_user_limit", "integer"),
        ("p_global_limit", "integer"),
    ]
    assert reserve["return_type"] == "jsonb"

    json_objects = re.findall(
        r"jsonb_build_object\((.*?)\)", reserve["body"], re.IGNORECASE | re.DOTALL
    )
    assert len(json_objects) == 3
    assert [re.findall(r"'([^']+)'\s*,", obj) for obj in json_objects] == [
        ["allowed", "reservation_id", "reason"],
        ["allowed", "reservation_id", "reason"],
        ["allowed", "reservation_id", "reason"],
    ]
    body = _normalized(reserve["body"])
    assert "'allowed', false, 'reservation_id', null, 'reason', 'user_limit'" in body
    assert "'allowed', false, 'reservation_id', null, 'reason', 'global_limit'" in body
    assert "'allowed', true, 'reservation_id', new_reservation::text, 'reason', null" in body
    _assert_service_role_grant(reserve, "reserve_ai_usage", RESERVE_SIGNATURE)


def test_006_reserves_two_slots_and_settles_the_actual_model_call_count():
    migration = MIGRATIONS / "006_model_call_quota_slots.sql"
    assert migration.exists(), "model-call quota semantics require a forward migration"
    sql = migration.read_text(encoding="utf-8")
    normalized = _normalized(sql)
    contracts = _parse_usage_functions(sql, migration.name)

    reserve = contracts[("reserve_ai_usage", RESERVE_SIGNATURE)]
    commit = contracts[("commit_ai_usage", COST_COMMIT_SIGNATURE)]
    rollback = contracts[("rollback_ai_usage", ROLLBACK_SIGNATURE)]

    assert "reserved_model_calls" in normalized
    assert "model_calls" in _normalized(reserve["body"])
    assert "pending" in _normalized(reserve["body"])
    assert "greatest(p_model_calls, 0)" in _normalized(commit["body"])
    assert "reserved_model_calls" in _normalized(commit["body"])
    assert "p_model_calls > reservation_slots" in _normalized(commit["body"])
    assert commit["return_type"] == "boolean"
    assert "status in ('reserved', 'expired')" in _normalized(commit["body"])
    assert "status in ('reserved', 'expired')" in _normalized(reserve["body"])
    assert "when reservation_status in ('reserved', 'expired')" in _normalized(
        commit["body"]
    )
    assert "return true" in _normalized(commit["body"])
    assert "raise exception" in _normalized(commit["body"])
    assert "reserved_model_calls" in _normalized(rollback["body"])
    _assert_service_role_grant(reserve, "reserve_ai_usage", RESERVE_SIGNATURE)
    _assert_service_role_grant(commit, "commit_ai_usage", COST_COMMIT_SIGNATURE)
    _assert_service_role_grant(rollback, "rollback_ai_usage", ROLLBACK_SIGNATURE)


def test_006_removes_cost_blind_legacy_usage_rpc_overloads():
    migration = MIGRATIONS / "006_model_call_quota_slots.sql"
    normalized = _normalized(migration.read_text(encoding="utf-8"))

    for signature in (
        "public.commit_ai_usage(uuid, text, date, integer, integer)",
        "public.commit_ai_usage(text, date, integer, integer)",
        "public.rollback_ai_usage(text, date)",
    ):
        assert f"drop function if exists {signature};" in normalized


def test_007_admits_each_call_on_its_utc_day_and_commit_does_not_double_count():
    migration = MIGRATIONS / "007_actual_invoke_day_quota.sql"
    assert migration.exists(), "actual-call-day admission requires a forward migration"
    sql = migration.read_text(encoding="utf-8")
    contracts = _parse_usage_functions(sql, migration.name)

    reserve = contracts[("reserve_ai_usage", RESERVE_SIGNATURE)]
    admit = contracts[("admit_ai_usage_call", ADMIT_CALL_SIGNATURE)]
    commit = contracts[("commit_ai_usage", COST_COMMIT_SIGNATURE)]
    rollback = contracts[("rollback_ai_usage", ROLLBACK_SIGNATURE)]
    reserve_body = _normalized(reserve["body"])
    admit_body = _normalized(admit["body"])
    commit_body = _normalized(commit["body"])
    rollback_body = _normalized(rollback["body"])

    assert "r.reserved_model_calls - r.incurred_model_calls" in reserve_body
    assert "least(p_reservation_date, p_call_usage_date)" in admit_body
    assert "greatest(p_reservation_date, p_call_usage_date)" in admit_body
    assert "incurred_model_calls = r.incurred_model_calls + 1" in admit_body
    assert "model_calls = ai_model_cost_counters.model_calls + 1" in admit_body
    assert "pending = pending - 1" in admit_body
    assert "p_model_calls <> incurred_model_calls" in commit_body
    assert "reservation_slots - incurred_model_calls" in commit_body
    assert "model_calls = ai_model_cost_counters.model_calls + excluded.model_calls" not in commit_body
    assert "reservation_slots - incurred_model_calls" in rollback_body
    _assert_service_role_grant(
        admit, "admit_ai_usage_call", ADMIT_CALL_SIGNATURE
    )
    _assert_service_role_grant(commit, "commit_ai_usage", COST_COMMIT_SIGNATURE)
    _assert_service_role_grant(rollback, "rollback_ai_usage", ROLLBACK_SIGNATURE)


def test_model_factory_disables_hidden_sdk_retries(monkeypatch):
    from app.agent import graph

    attempts = []

    class FakeChatDeepSeek:
        def __init__(self, **kwargs):
            self.max_retries = kwargs["max_retries"]

        def invoke(self, _messages):
            attempts.extend(range(self.max_retries + 1))
            raise RuntimeError("upstream unavailable")

    monkeypatch.setattr(graph, "ChatDeepSeek", FakeChatDeepSeek)
    monkeypatch.setattr(
        graph,
        "get_settings",
        lambda: SimpleNamespace(
            **{
                "deepseek_api" + "_key": SimpleNamespace(
                    get_secret_value=lambda: "test-key"
                )
            },
            deepseek_model="test-model",
            deepseek_api_base="https://provider.invalid",
        ),
    )

    with pytest.raises(RuntimeError, match="upstream unavailable"):
        graph.model().invoke([])

    assert attempts == [0]


def test_legacy_intent_model_disables_hidden_sdk_retries(monkeypatch):
    from app.agent import intent as intent_module

    attempts = []

    class FakeChatDeepSeek:
        def __init__(self, **kwargs):
            self.max_retries = kwargs["max_retries"]

        def invoke(self, _messages):
            attempts.extend(range(self.max_retries + 1))
            raise RuntimeError("upstream unavailable")

    monkeypatch.setattr(intent_module, "ChatDeepSeek", FakeChatDeepSeek)
    monkeypatch.setattr(
        intent_module,
        "get_settings",
        lambda: SimpleNamespace(
            **{
                "deepseek_api" + "_key": SimpleNamespace(
                    get_secret_value=lambda: "test-key"
                )
            },
            deepseek_model="test-model",
            deepseek_api_base="https://provider.invalid",
        ),
    )

    with pytest.raises(RuntimeError, match="upstream unavailable"):
        intent_module.intent_model().invoke([])

    assert attempts == [0]


def test_service_role_repository_uses_exact_complete_reservation_rpc_contract():
    calls = []
    contracts = _final_usage_contracts()
    reserve_contract = contracts[("reserve_ai_usage", RESERVE_SIGNATURE)]
    json_keys = re.findall(
        r"'([^']+)'\s*,",
        re.findall(
            r"jsonb_build_object\((.*?)\)",
            reserve_contract["body"],
            re.IGNORECASE | re.DOTALL,
        )[-1],
    )
    assert json_keys == ["allowed", "reservation_id", "reason"]

    class Query:
        def __init__(self, name, args): self.name, self.args = name, args
        def execute(self):
            calls.append((self.name, self.args))
            if self.name == "reserve_ai_usage":
                data = dict(zip(json_keys, (True, RESERVATION_ID, None), strict=True))
            elif self.name == "admit_ai_usage_call":
                data = {"allowed": True, "reason": None}
            else:
                data = True
            return type("Result", (), {"data": data})()
    class Client:
        def rpc(self, name, args): return Query(name, args)
    repo = SupabaseUsageRepository(Client())
    result = repo.reserve("user:1", TODAY, 5, 100)
    assert result == ReserveResult(RESERVATION_ID, None)
    admitted = repo.admit_model_call(
        result.reservation_id,
        "user:1",
        TODAY,
        TODAY + timedelta(days=1),
        5,
        100,
    )
    assert admitted == CallAdmissionResult(True, None)
    repo.commit(result.reservation_id, "user:1", TODAY, 3, 4, 2, 17)
    repo.rollback(result.reservation_id, "user:1", TODAY)

    expected_contracts = [
        contracts[("reserve_ai_usage", RESERVE_SIGNATURE)],
        contracts[("admit_ai_usage_call", ADMIT_CALL_SIGNATURE)],
        contracts[("commit_ai_usage", COST_COMMIT_SIGNATURE)],
        contracts[("rollback_ai_usage", ROLLBACK_SIGNATURE)],
    ]
    assert [name for name, _ in calls] == [
        "reserve_ai_usage",
        "admit_ai_usage_call",
        "commit_ai_usage",
        "rollback_ai_usage",
    ]
    assert [list(args) for _, args in calls] == [
        [name for name, _ in contract["parameters"]]
        for contract in expected_contracts
    ]
    assert [list(args.values()) for _, args in calls] == [
        ["user:1", "2026-07-29", 5, 100],
        [RESERVATION_ID, "user:1", "2026-07-29", "2026-07-30", 5, 100],
        [RESERVATION_ID, "user:1", "2026-07-29", 3, 4, 2, 17],
        [RESERVATION_ID, "user:1", "2026-07-29"],
    ]


def test_supabase_usage_commit_fails_closed_when_database_did_not_settle():
    class Query:
        def execute(self):
            return SimpleNamespace(data=False)

    class Client:
        def rpc(self, _name, _args):
            return Query()

    repository = SupabaseUsageRepository(Client())

    with pytest.raises(ProviderUnavailable):
        repository.commit(RESERVATION_ID, "user:1", TODAY, 3, 4, 1, 17)


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        ([{"allowed": False, "reservation_id": None, "reason": "user_limit"}], ReserveResult(None, "user_limit")),
        ({"allowed": False, "reservation_id": None, "reason": "global_limit"}, ReserveResult(None, "global_limit")),
    ],
)
def test_service_role_repository_normalizes_valid_limit_responses(data, expected):
    class Client:
        def rpc(self, _name, _args):
            return SimpleNamespace(execute=lambda: SimpleNamespace(data=data))

    assert SupabaseUsageRepository(Client()).reserve("user:1", TODAY, 5, 100) == expected


@pytest.mark.parametrize(
    "data",
    [
        RESERVATION_ID,
        [],
        [{"allowed": True, "reservation_id": RESERVATION_ID}],
        {"allowed": False, "reservation_id": None, "reason": "unknown_limit"},
        {"allowed": "true", "reservation_id": RESERVATION_ID, "reason": None},
        {"allowed": True, "reservation_id": "not-a-uuid", "reason": None},
        {"allowed": True, "reservation_id": RESERVATION_ID, "reason": None, "extra": "field"},
    ],
)
def test_service_role_repository_fails_closed_on_malformed_or_unknown_responses(data):
    class Client:
        def rpc(self, _name, _args):
            return SimpleNamespace(execute=lambda: SimpleNamespace(data=data))

    with pytest.raises(ProviderUnavailable) as error:
        SupabaseUsageRepository(Client()).reserve("user:1", TODAY, 5, 100)

    assert error.value.code == "AI_UNAVAILABLE"


def test_service_role_repository_maps_rpc_exceptions_to_a_stable_upstream_error():
    class Client:
        def rpc(self, _name, _args):
            return SimpleNamespace(execute=lambda: (_ for _ in ()).throw(RuntimeError("raw upstream body")))

    with pytest.raises(ProviderUnavailable) as error:
        SupabaseUsageRepository(Client()).reserve("user:1", TODAY, 5, 100)

    assert error.value.code == "AI_UNAVAILABLE"
    assert "raw upstream body" not in str(error.value)


def test_usage_repository_logs_correlated_rpc_failure_without_raw_subject(caplog):
    from app.core.logging import correlation_context

    class Client:
        def rpc(self, _name, _args):
            return SimpleNamespace(
                execute=lambda: (_ for _ in ()).throw(RuntimeError("raw database body"))
            )

    with correlation_context("req-db-usage", "anon-network:digest"), caplog.at_level(
        logging.WARNING, logger="app.database"
    ):
        with pytest.raises(ProviderUnavailable):
            SupabaseUsageRepository(Client()).reserve("private-subject", TODAY, 5, 100)

    record = next(record for record in caplog.records if record.message == "database_result")
    assert record.request_id == "req-db-usage"
    assert record.subject == "anon-network:digest"
    assert record.db_operation == "usage.reserve"
    assert record.db_status == "failure"
    assert "private-subject" not in caplog.text
    assert "raw database body" not in caplog.text


def test_production_usage_wiring_creates_the_repository_with_the_service_role_key(monkeypatch):
    calls = []
    client = object()
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("DEEPSEEK_API" + "_KEY", "test-key")
    monkeypatch.setenv("SUPABASE_URL", "https://project.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "anon-key")
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "service-role-key")
    monkeypatch.setenv("ANON_SESSION_SIGNING_SECRET", "AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8")
    monkeypatch.setitem(sys.modules, "supabase", SimpleNamespace(create_client=lambda url, key: calls.append((url, key)) or client))
    get_settings.cache_clear()

    guard = get_usage_guard()

    assert isinstance(guard.repository, SupabaseUsageRepository)
    assert guard.repository._client is client
    assert calls == [("https://project.supabase.co/", "service-role-key")]
    get_settings.cache_clear()


def test_atomic_reserve_results_do_not_cross_contaminate_failure_reasons():
    repository = InMemoryUsageRepository()
    for _ in range(2):
        result = repository.reserve("full", TODAY, 5, 100)
        for _ in range(2):
            assert repository.admit_model_call(
                result.reservation_id, "full", TODAY, TODAY, 5, 100
            ).admitted
        repository.commit(result.reservation_id, "full", TODAY, 0, 0, 2, 0)
    user_result = repository.reserve("full", TODAY, 5, 100)
    global_result = repository.reserve("other", TODAY, 5, 0)
    assert user_result.failure_reason == "user_limit"
    assert global_result.failure_reason == "global_limit"
