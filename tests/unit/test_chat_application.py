from pathlib import Path
from uuid import UUID

from app.agent.graph import ChatResult
from app.application.chat import ConfirmationStore, TravelChatApplication
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
        def plan_confirmed(self, *args):
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
        def plan_confirmed(self, *_args):
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
