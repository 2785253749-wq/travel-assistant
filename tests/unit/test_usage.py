from __future__ import annotations

from datetime import UTC, datetime
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
import re
import sys

import pytest

from app.core.errors import AppError
from app.core.config import get_settings
from app.core.usage import InMemoryUsageRepository, ModelGateway, ProviderCircuitBreaker, ProviderUnavailable, ReserveResult, SupabaseUsageRepository, UsageGuard, classify_provider_error, get_usage_guard, model_usage_scope


TODAY = datetime(2026, 7, 29, tzinfo=UTC).date()
RESERVATION_ID = "11111111-1111-4111-8111-111111111111"


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


def test_reserve_sql_declares_the_structured_jsonb_contract_for_every_branch():
    migration = Path(__file__).parents[2] / "supabase" / "migrations" / "002_ai_usage_reservations.sql"
    sql = migration.read_text(encoding="utf-8")
    definition = re.search(
        r"create or replace function public\.reserve_ai_usage\(.*?\$\$;",
        sql,
        re.IGNORECASE | re.DOTALL,
    )

    assert definition is not None
    normalized = " ".join(definition.group(0).lower().split())
    assert ") returns jsonb language plpgsql" in normalized
    assert normalized.count("jsonb_build_object(") == 3
    assert "'allowed', false, 'reservation_id', null, 'reason', 'user_limit'" in normalized
    assert "'allowed', false, 'reservation_id', null, 'reason', 'global_limit'" in normalized
    assert "'allowed', true, 'reservation_id', new_reservation::text, 'reason', null" in normalized


def test_service_role_repository_uses_exact_complete_reservation_rpc_contract():
    calls = []
    class Query:
        def __init__(self, name, args): self.name, self.args = name, args
        def execute(self):
            calls.append((self.name, self.args))
            return type("Result", (), {"data": {"allowed": True, "reservation_id": RESERVATION_ID, "reason": None}})()
    class Client:
        def rpc(self, name, args): return Query(name, args)
    repo = SupabaseUsageRepository(Client())
    result = repo.reserve("user:1", TODAY, 5, 100)
    assert result == ReserveResult(RESERVATION_ID, None)
    repo.commit(result.reservation_id, "user:1", TODAY, 3, 4)
    repo.rollback(result.reservation_id, "user:1", TODAY)
    assert calls == [
        ("reserve_ai_usage", {"p_subject_key": "user:1", "p_usage_date": "2026-07-29", "p_user_limit": 5, "p_global_limit": 100}),
        ("commit_ai_usage", {"p_reservation_id": RESERVATION_ID, "p_subject_key": "user:1", "p_usage_date": "2026-07-29", "p_input_tokens": 3, "p_output_tokens": 4}),
        ("rollback_ai_usage", {"p_reservation_id": RESERVATION_ID, "p_subject_key": "user:1", "p_usage_date": "2026-07-29"}),
    ]


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


def test_production_usage_wiring_creates_the_repository_with_the_service_role_key(monkeypatch):
    calls = []
    client = object()
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek-key")
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
    repository.set_user_count("full", 5, day=TODAY)
    user_result = repository.reserve("full", TODAY, 5, 100)
    global_result = repository.reserve("other", TODAY, 5, 0)
    assert user_result.failure_reason == "user_limit"
    assert global_result.failure_reason == "global_limit"
