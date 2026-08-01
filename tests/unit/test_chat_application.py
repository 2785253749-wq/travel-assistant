from app.agent.graph import ChatResult
from app.application.chat import ConfirmationStore, TravelChatApplication
from app.schemas import TravelProfile


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
