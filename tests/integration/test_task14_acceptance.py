from datetime import UTC, datetime
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from app.api.auth import AuthenticatedUser, get_supabase_auth_gateway_factory
from app.main import app
from app.travel_notes.interactions import (
    InMemoryInteractionRepository,
    TravelNoteInteractionModule,
)
from app.travel_notes.moderation import (
    InMemoryModerationRepository,
    ModerationComment,
    ModerationImage,
    ModerationNote,
    ModerationReport,
    TravelNoteModerationModule,
)


USER_A = UUID("11111111-1111-1111-1111-111111111111")
USER_B = UUID("22222222-2222-2222-2222-222222222222")
ADMIN = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
NOTE_ID = UUID("cccccccc-cccc-cccc-cccc-cccccccccccc")
COMMENT_ID = UUID("dddddddd-dddd-dddd-dddd-dddddddddddd")
REPORT_ID = UUID("eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee")


class FakeAuthGateway:
    def get_user(self, token: str) -> AuthenticatedUser:
        users = {
            "user-a": AuthenticatedUser(USER_A, "alice@example.com"),
            "user-b": AuthenticatedUser(USER_B, "bob@example.com"),
            "admin": AuthenticatedUser(ADMIN, "admin@example.com"),
        }
        return users[token]


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def interaction_client():
    from app.composition import (
        get_optional_travel_note_interaction_module,
        get_travel_note_interaction_module,
    )

    repository = InMemoryInteractionRepository(approved_note_ids={NOTE_ID})
    module = TravelNoteInteractionModule(repository, repository)
    app.dependency_overrides[get_supabase_auth_gateway_factory] = (
        lambda: lambda: FakeAuthGateway()
    )
    app.dependency_overrides[get_travel_note_interaction_module] = lambda: module
    app.dependency_overrides[get_optional_travel_note_interaction_module] = (
        lambda: module
    )
    with TestClient(app) as client:
        yield client, repository
    app.dependency_overrides.clear()


def test_anonymous_and_two_accounts_keep_interactions_and_pending_comments_isolated(
    interaction_client,
):
    client, repository = interaction_client

    assert client.get(f"/api/community/notes/{NOTE_ID}/comments").json() == {
        "items": [],
        "next_cursor": None,
    }

    liked = client.put(
        f"/api/community/notes/{NOTE_ID}/like", headers=_headers("user-a")
    )
    bookmarked = client.put(
        f"/api/community/notes/{NOTE_ID}/bookmark", headers=_headers("user-a")
    )
    comment = client.post(
        f"/api/community/notes/{NOTE_ID}/comments",
        headers=_headers("user-a"),
        json={"body": "等待审核的评论"},
    )

    assert liked.status_code == 200
    assert liked.json()["liked"] is True
    assert bookmarked.status_code == 200
    assert bookmarked.json()["bookmarked"] is True
    assert comment.status_code == 201
    assert comment.json()["status"] == "pending_review"
    assert client.get(f"/api/community/notes/{NOTE_ID}/comments").json()["items"] == []

    # Account B has no access to A's like/bookmark state, while both accounts
    # can independently submit pending comments without making them public.
    b_state = repository.viewer_state(USER_B, NOTE_ID)
    a_state = repository.viewer_state(USER_A, NOTE_ID)
    assert b_state.liked is False
    assert b_state.bookmarked is False
    assert a_state.liked is True
    assert a_state.bookmarked is True
    assert len(repository._comments) == 1

    mismatched_report = client.post(
        f"/api/community/notes/{NOTE_ID}/reports",
        headers=_headers("user-b"),
        json={
            "target_type": "comment",
            "target_id": str(UUID(int=0)),
            "reason": "不应跨游记举报",
        },
    )
    assert mismatched_report.status_code == 404


@pytest.fixture
def moderation_client():
    from app.composition import get_community_moderation_module

    now = datetime(2026, 8, 22, 9, 0, tzinfo=UTC)
    repository = InMemoryModerationRepository(admin_user_ids={ADMIN})
    repository.add_note(
        ModerationNote(
            id=NOTE_ID,
            title="待审游记",
            body="仅管理员可见的待审正文",
            location_name="厦门",
            category="城市漫步",
            status="pending_review",
            review_reason=None,
            submitted_at=now,
            author_display_name="Voyage 旅行者",
            images=[
                ModerationImage(
                    id=UUID("ffffffff-ffff-ffff-ffff-ffffffffffff"),
                    image_url="https://signed.example.test/short-lived",
                    sort_order=0,
                    width=1200,
                    height=800,
                )
            ],
        )
    )
    repository.add_comment(
        ModerationComment(
            id=COMMENT_ID,
            note_id=NOTE_ID,
            author_display_name="Voyage 旅行者",
            body="待审评论",
            status="pending_review",
            review_reason=None,
            created_at=now,
        )
    )
    repository.add_report(
        ModerationReport(
            id=REPORT_ID,
            target_type="comment",
            target_id=COMMENT_ID,
            reason="疑似广告",
            status="pending",
            resolution_note=None,
            created_at=now,
        )
    )
    app.dependency_overrides[get_supabase_auth_gateway_factory] = (
        lambda: lambda: FakeAuthGateway()
    )
    app.dependency_overrides[get_community_moderation_module] = lambda: TravelNoteModerationModule(
        repository
    )
    with TestClient(app) as client:
        yield client, repository
    app.dependency_overrides.clear()


def test_admin_journey_is_allowlisted_and_report_action_is_explicit(
    moderation_client, caplog
):
    client, repository = moderation_client

    # Task 13 replaced the per-queue Task 12 paths with one typed review queue.
    queue_path = "/api/admin/community/review-queue"
    anonymous = client.get(f"{queue_path}?target_type=note")
    non_admin = client.get(
        f"{queue_path}?target_type=note", headers=_headers("user-a")
    )
    with caplog.at_level("INFO"):
        notes = client.get(
            f"{queue_path}?target_type=note", headers=_headers("admin")
        )
        comments = client.get(
            f"{queue_path}?target_type=comment", headers=_headers("admin")
        )
        reports = client.get(
            f"{queue_path}?target_type=report", headers=_headers("admin")
        )

    assert anonymous.status_code == 401
    assert non_admin.status_code == 403
    assert notes.status_code == comments.status_code == reports.status_code == 200
    assert notes.json()["items"][0]["images"][0]["image_url"].startswith(
        "https://signed.example.test/"
    )
    for response in (notes, comments, reports):
        assert "storage_path" not in response.text
        assert "author_id" not in response.text
        assert "@example.com" not in response.text
    assert "storage_path" not in caplog.text
    assert "@example.com" not in caplog.text

    actioned = client.post(
        f"/api/admin/community/reports/{REPORT_ID}/resolve",
        headers=_headers("admin"),
        json={"decision": "actioned", "resolution_note": "已处理举报"},
    )
    assert actioned.status_code == 200
    assert actioned.json()["status"] == "actioned"
    # Closing a report is not an implicit content moderation operation.
    assert repository._comments[COMMENT_ID].status == "pending_review"


def test_runtime_config_keeps_service_role_backend_only(moderation_client, monkeypatch):
    from app.core.config import get_settings

    client, _ = moderation_client
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "server-only-secret")
    get_settings.cache_clear()
    runtime = client.get("/runtime-config.js")
    get_settings.cache_clear()

    assert runtime.status_code == 200
    assert "server-only-secret" not in runtime.text
    assert "SUPABASE_SERVICE_KEY" not in runtime.text
