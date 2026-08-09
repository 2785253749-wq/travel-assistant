from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

from app.agent.graph import ChatResult
from app.application.chat import ConfirmationStore, TravelChatApplication
from app.core.usage import ModelGateway, ProviderUnavailable
from app.schemas import Itinerary, TravelProfile
from app.trips.models import Trip


def test_confirm_reserves_against_quota_subject_not_cookie_conversation_scope():
    profile = TravelProfile(
        origin="上海",
        destination="杭州",
        start_date="2026-10-01",
        end_date="2026-10-02",
        travelers=2,
        budget_cny=3000,
    )
    store = ConfirmationStore()
    store.put("anon-cookie:rotating", "thread-1", None, profile, "plan")

    class Reservation:
        def rollback(self):
            pass

    class Guard:
        subjects = []

        def reserve(self, subject):
            self.subjects.append(subject)
            return Reservation()

    class Agent:
        def plan_confirmed(self, *args, **kwargs):
            return ChatResult("try later", "collecting", profile.model_dump())

    guard = Guard()
    application = TravelChatApplication(
        agent_factory=lambda _: Agent(),
        usage_guard=guard,
        confirmation_store=store,
    )

    application.confirm(
        user_id=None,
        subject="anon-cookie:rotating",
        quota_subject="anon-network:stable",
        thread_id="thread-1",
        trip_id=None,
        message="confirm",
    )

    assert guard.subjects == ["anon-network:stable"]


def test_confirmation_error_carries_the_claimed_business_intent():
    profile = TravelProfile(
        origin="上海",
        destination="杭州",
        start_date="2026-10-01",
        end_date="2026-10-02",
        travelers=2,
        budget_cny=3000,
    )
    store = ConfirmationStore()
    store.put(
        "anon:modify",
        "thread-modify-error",
        None,
        profile,
        "第二天换西湖",
        "modify_trip",
    )

    class Guard:
        def reserve(self, _subject):
            raise ProviderUnavailable()

    application = TravelChatApplication(
        agent_factory=lambda _: object(),
        usage_guard=Guard(),
        confirmation_store=store,
    )

    try:
        application.confirm(
            user_id=None,
            subject="anon:modify",
            quota_subject="anon-network:modify",
            thread_id="thread-modify-error",
            trip_id=None,
            message="确认",
        )
    except ProviderUnavailable as exc:
        assert getattr(exc, "intent", None) == "modify_trip"
    else:
        raise AssertionError("provider failure must propagate with the claimed intent")


def test_authenticated_confirmation_persists_plan_and_messages_atomically_before_usage_commit():
    user_id = UUID("11111111-1111-1111-1111-111111111111")
    profile = TravelProfile(
        origin="上海",
        destination="成都",
        start_date="2026-10-01",
        end_date="2026-10-02",
        travelers=2,
        budget_cny=5000,
    )
    itinerary = Itinerary.model_validate_json(
        Path("tests/fixtures/task7_itinerary.json").read_text(encoding="utf-8")
    )
    store = ConfirmationStore()
    store.put("user:owner", "thread-1", None, profile, "请规划成都行程")
    events: list[str] = []

    class Reservation:
        def commit(self, *_args):
            events.append("commit")

        def rollback(self):
            events.append("rollback")

    class Guard:
        def reserve(self, _subject):
            events.append("reserve")
            return Reservation()

    class Agent:
        def plan_confirmed(self, *_args, **_kwargs):
            events.append("plan")
            return ChatResult("# 成都行程\n\n可读摘要", "planned", profile.model_dump(), itinerary=itinerary)

    class AtomicTripService:
        calls = []

        def get_trip(self, *_args):
            raise AssertionError("a new confirmation must not load a missing trip")

        def persist_planned_chat(self, owner_id, trip, planned_profile, planned_itinerary, user_message, assistant_message):
            events.append("persist")
            self.calls.append(
                (owner_id, trip, planned_profile, planned_itinerary, user_message, assistant_message)
            )
            return Trip(
                user_id=owner_id,
                title="成都 trip",
                profile=planned_profile,
                status="planned",
                itinerary=planned_itinerary,
            )

    service = AtomicTripService()
    application = TravelChatApplication(
        agent_factory=lambda _: Agent(),
        usage_guard=Guard(),
        confirmation_store=store,
        trip_service=service,
    )

    result = application.confirm(
        user_id=user_id,
        subject="user:owner",
        quota_subject="user:owner",
        thread_id="thread-1",
        trip_id=None,
        message="确认",
    )

    assert events == ["reserve", "plan", "persist", "commit"]
    assert len(service.calls) == 1
    assert service.calls[0][4:] == ("请规划成都行程", "# 成都行程\n\n可读摘要")
    assert result.trip_id is not None


def test_modify_intent_and_instruction_survive_collection_until_confirmation():
    user_id = UUID("11111111-1111-1111-1111-111111111111")
    profile = TravelProfile(
        origin="上海",
        destination="成都",
        start_date="2026-10-01",
        end_date="2026-10-02",
        travelers=2,
        budget_cny=5000,
    )
    itinerary = Itinerary.model_validate_json(
        Path("tests/fixtures/task7_itinerary.json").read_text(encoding="utf-8")
    )
    trip = Trip(
        user_id=user_id,
        title="成都 trip",
        profile=profile,
        status="planned",
        itinerary=itinerary,
    )
    seen = []

    class Reservation:
        def commit(self, *_args):
            pass

        def rollback(self):
            pass

    class Guard:
        def reserve(self, _subject):
            return Reservation()

    class Agent:
        def collect(self, message, loaded_trip):
            assert loaded_trip is trip
            return ChatResult(
                "请确认修改",
                "confirming",
                profile.model_dump(),
                itinerary=itinerary,
                intent="modify_trip",
            )

        def plan_confirmed(self, planned_profile, loaded_trip, owner_id, message, *, intent):
            seen.append((loaded_trip, owner_id, message, intent))
            return ChatResult(
                "# 修改后的成都行程",
                "planned",
                planned_profile.model_dump(),
                itinerary=itinerary,
                intent=intent,
            )

    class TripService:
        def get_trip(self, *_args):
            return trip

        def persist_planned_chat(self, *_args):
            return trip

    application = TravelChatApplication(
        agent_factory=lambda _: Agent(),
        usage_guard=Guard(),
        confirmation_store=ConfirmationStore(),
        trip_service=TripService(),
    )

    collected = application.collect(
        user_id=user_id,
        subject="user:owner",
        thread_id="thread-1",
        trip_id=trip.id,
        message="把第二天改成西湖",
    )
    confirmed = application.confirm(
        user_id=user_id,
        subject="user:owner",
        quota_subject="user:owner",
        thread_id="thread-1",
        trip_id=trip.id,
        message="确认",
    )

    assert collected.intent == "modify_trip"
    assert confirmed.intent == "modify_trip"
    assert seen == [(trip, user_id, "把第二天改成西湖", "modify_trip")]


def test_failed_model_attempt_is_committed_instead_of_rolled_back():
    profile = TravelProfile(
        origin="上海",
        destination="成都",
        start_date="2026-10-01",
        end_date="2026-10-02",
        travelers=2,
        budget_cny=5000,
    )
    store = ConfirmationStore()
    store.put("anon:session", "thread-cost", None, profile, "规划成都")
    events = []

    class Reservation:
        def admit_model_call(self):
            events.append(("admit",))

        def commit(self, input_tokens=0, output_tokens=0, model_calls=0):
            events.append(("commit", input_tokens, output_tokens, model_calls))

        def rollback(self):
            events.append(("rollback",))

    class Guard:
        def reserve(self, _subject):
            return Reservation()

    class BrokenModel:
        def invoke(self, _messages):
            raise RuntimeError("provider failed after request")

    gateway = ModelGateway(lambda: BrokenModel())

    class Agent:
        def plan_confirmed(self, *_args, **_kwargs):
            gateway.invoke([])

    application = TravelChatApplication(
        agent_factory=lambda _: Agent(),
        usage_guard=Guard(),
        confirmation_store=store,
    )

    try:
        application.confirm(
            user_id=None,
            subject="anon:session",
            quota_subject="anon-network:stable",
            thread_id="thread-cost",
            trip_id=None,
            message="确认",
        )
    except ProviderUnavailable:
        pass
    else:
        raise AssertionError("provider failure must still propagate")

    assert events == [("admit",), ("commit", 0, 0, 1)]


def test_usage_commit_failure_after_atomic_persistence_does_not_turn_success_into_503():
    user_id = UUID("11111111-1111-1111-1111-111111111111")
    profile = TravelProfile(
        origin="上海",
        destination="成都",
        start_date="2026-10-01",
        end_date="2026-10-02",
        travelers=2,
        budget_cny=5000,
    )
    itinerary = Itinerary.model_validate_json(
        Path("tests/fixtures/task7_itinerary.json").read_text(encoding="utf-8")
    )
    store = ConfirmationStore()
    store.put("user:owner", "thread-usage", None, profile, "规划成都")

    class Reservation:
        def commit(self, *_args, **_kwargs):
            raise RuntimeError("usage store unavailable")

        def rollback(self):
            raise AssertionError("a persisted plan must not be rolled back in a separate store")

    class Guard:
        def reserve(self, _subject):
            return Reservation()

    class Agent:
        def plan_confirmed(self, *_args, **_kwargs):
            return ChatResult(
                "# 成都行程", "planned", profile.model_dump(), itinerary=itinerary
            )

    class Service:
        def persist_planned_chat(self, owner_id, *_args):
            return Trip(
                user_id=owner_id,
                title="成都 trip",
                profile=profile,
                status="planned",
                itinerary=itinerary,
            )

    result = TravelChatApplication(
        agent_factory=lambda _: Agent(),
        usage_guard=Guard(),
        confirmation_store=store,
        trip_service=Service(),
    ).confirm(
        user_id=user_id,
        subject="user:owner",
        quota_subject="user:owner",
        thread_id="thread-usage",
        trip_id=None,
        message="确认",
    )

    assert result.stage == "planned"
    assert result.trip_id is not None
    assert result.warnings == [
        "用量结算暂不可用；本次预留额度已按最坏情况继续占用。"
    ]


def test_usage_commit_failure_on_a_nonplanned_result_surfaces_fail_closed_warning():
    profile = TravelProfile(
        origin="上海",
        destination="成都",
        start_date="2026-10-01",
        end_date="2026-10-02",
        travelers=2,
        budget_cny=5000,
    )
    store = ConfirmationStore()
    store.put("anon:session", "thread-unsettled", None, profile, "规划成都")

    class Reservation:
        def admit_model_call(self):
            pass

        def commit(self, *_args, **_kwargs):
            raise RuntimeError("usage store unavailable")

        def rollback(self):
            raise AssertionError("an incurred model call must retain its reservation")

    class Guard:
        def reserve(self, _subject):
            return Reservation()

    gateway = ModelGateway(
        lambda: SimpleNamespace(
            invoke=lambda _messages: SimpleNamespace(
                content="invalid plan",
                usage_metadata={"input_tokens": 2, "output_tokens": 3},
            )
        )
    )

    class Agent:
        def plan_confirmed(self, *_args, **_kwargs):
            gateway.invoke([])
            return ChatResult(
                "暂时无法生成行程", "collecting", profile.model_dump()
            )

    result = TravelChatApplication(
        agent_factory=lambda _: Agent(),
        usage_guard=Guard(),
        confirmation_store=store,
    ).confirm(
        user_id=None,
        subject="anon:session",
        quota_subject="anon-network:stable",
        thread_id="thread-unsettled",
        trip_id=None,
        message="确认",
    )

    assert result.stage == "collecting"
    assert result.warnings == [
        "用量结算暂不可用；本次预留额度已按最坏情况继续占用。"
    ]


def test_successful_confirmation_consumes_pending_and_replay_never_reserves_again():
    from app.core.errors import AppError

    profile = TravelProfile(
        origin="上海",
        destination="成都",
        start_date="2026-10-01",
        end_date="2026-10-02",
        travelers=2,
        budget_cny=5000,
    )
    itinerary = Itinerary.model_validate_json(
        Path("tests/fixtures/task7_itinerary.json").read_text(encoding="utf-8")
    )
    store = ConfirmationStore()
    store.put("anon:once", "thread-once", None, profile, "规划成都")
    reservations = []

    class Reservation:
        def commit(self, *_args):
            pass

        def rollback(self):
            pass

    class Guard:
        def reserve(self, _subject):
            reservations.append(1)
            return Reservation()

    class Agent:
        def plan_confirmed(self, *_args, **_kwargs):
            return ChatResult(
                "# 成都行程", "planned", profile.model_dump(), itinerary=itinerary
            )

    application = TravelChatApplication(
        agent_factory=lambda _: Agent(),
        usage_guard=Guard(),
        confirmation_store=store,
    )

    assert application.confirm(
        user_id=None,
        subject="anon:once",
        quota_subject="anon-network:once",
        thread_id="thread-once",
        trip_id=None,
        message="确认",
    ).stage == "planned"
    try:
        application.confirm(
            user_id=None,
            subject="anon:once",
            quota_subject="anon-network:once",
            thread_id="thread-once",
            trip_id=None,
            message="确认",
        )
    except AppError as exc:
        assert exc.code == "CONFIRMATION_REQUIRED"
    else:
        raise AssertionError("a consumed confirmation must not be replayable")

    assert reservations == [1]


def test_concurrent_confirmation_claim_allows_only_one_planning_attempt():
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event, Lock
    from time import monotonic, sleep

    from app.core.errors import AppError

    profile = TravelProfile(
        origin="上海",
        destination="成都",
        start_date="2026-10-01",
        end_date="2026-10-02",
        travelers=2,
        budget_cny=5000,
    )
    itinerary = Itinerary.model_validate_json(
        Path("tests/fixtures/task7_itinerary.json").read_text(encoding="utf-8")
    )
    store = ConfirmationStore()
    store.put("anon:claim", "thread-claim", None, profile, "规划成都")
    release = Event()
    entered = Event()
    calls = []
    calls_lock = Lock()
    reservations = []

    class Reservation:
        def commit(self, *_args, **_kwargs):
            pass

        def rollback(self):
            pass

    class Guard:
        def reserve(self, _subject):
            reservations.append(1)
            return Reservation()

    class Agent:
        def plan_confirmed(self, *_args, **_kwargs):
            with calls_lock:
                calls.append(1)
                entered.set()
            assert release.wait(2)
            return ChatResult(
                "ok", "planned", profile.model_dump(), itinerary=itinerary
            )

    application = TravelChatApplication(
        agent_factory=lambda _: Agent(),
        usage_guard=Guard(),
        confirmation_store=store,
    )

    def confirm_once():
        try:
            return application.confirm(
                user_id=None,
                subject="anon:claim",
                quota_subject="anon-network:claim",
                thread_id="thread-claim",
                trip_id=None,
                message="确认",
            )
        except AppError as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(confirm_once)
        assert entered.wait(1)
        second = pool.submit(confirm_once)
        deadline = monotonic() + 1
        while len(calls) < 2 and not second.done() and monotonic() < deadline:
            sleep(0.001)
        release.set()
        outcomes = [first.result(), second.result()]

    assert len(calls) == 1
    assert len(reservations) == 1
    assert sum(isinstance(outcome, ChatResult) for outcome in outcomes) == 1
    assert outcomes.count("CONFIRMATION_REQUIRED") == 1


def test_explain_result_discards_a_stale_modify_confirmation():
    user_id = UUID("11111111-1111-1111-1111-111111111111")
    profile = TravelProfile(
        origin="上海",
        destination="成都",
        start_date="2026-10-01",
        end_date="2026-10-02",
        travelers=2,
        budget_cny=5000,
    )
    itinerary = Itinerary.model_validate_json(
        Path("tests/fixtures/task7_itinerary.json").read_text(encoding="utf-8")
    )
    trip = Trip(
        user_id=user_id,
        title="成都 trip",
        profile=profile,
        status="planned",
        itinerary=itinerary,
    )
    store = ConfirmationStore()
    store.put(
        "user:owner",
        "thread-explain",
        trip.id,
        profile.model_copy(update={"budget_cny": 6000}),
        "预算改为6000元",
        "modify_trip",
    )

    class Agent:
        def collect(self, *_args):
            return ChatResult(
                "只依据已保存行程解释",
                "planned",
                profile.model_dump(),
                itinerary=itinerary,
                intent="explain_trip",
            )

    class Service:
        def get_trip(self, *_args):
            return trip

    application = TravelChatApplication(
        agent_factory=lambda _: Agent(),
        usage_guard=object(),
        confirmation_store=store,
        trip_service=Service(),
    )
    result = application.collect(
        user_id=user_id,
        subject="user:owner",
        thread_id="thread-explain",
        trip_id=trip.id,
        message="为什么这样安排？",
    )

    assert result.intent == "explain_trip"
    assert store.get("user:owner", "thread-explain", trip.id) is None
