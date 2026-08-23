from datetime import UTC, datetime
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from app.api.auth import AuthenticatedUser, get_supabase_auth_gateway_factory
from app.main import app
from app.travel_notes.moderation import (
    InMemoryModerationRepository,
    ModerationComment,
    ModerationImage,
    ModerationNote,
    ModerationReport,
    TravelNoteModerationModule,
)


ADMIN_ID = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
USER_ID = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
NOTE_ID = UUID("cccccccc-cccc-cccc-cccc-cccccccccccc")
COMMENT_ID = UUID("dddddddd-dddd-dddd-dddd-dddddddddddd")
REPORT_ID = UUID("eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee")


class FakeAuthGateway:
    def get_user(self, token: str) -> AuthenticatedUser:
        if token == "admin":
            return AuthenticatedUser(id=ADMIN_ID, email="admin@example.com")
        if token == "user":
            return AuthenticatedUser(id=USER_ID, email="user@example.com")
        raise RuntimeError("unexpected token")


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def client():
    now = datetime(2026, 8, 22, 9, 0, tzinfo=UTC)
    repository = InMemoryModerationRepository(admin_user_ids={ADMIN_ID})
    repository.add_note(
        ModerationNote(
            id=NOTE_ID,
            title="审核中的游记",
            body="管理员可见内容",
            location_name="厦门",
            category="城市漫步",
            status="pending_review",
            review_reason=None,
            submitted_at=now,
            author_display_name="Voyage 旅行者",
            images=[
                ModerationImage(
                    id=UUID("ffffffff-ffff-ffff-ffff-ffffffffffff"),
                    image_url="https://signed.example.test/ttl",
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
            body="审核中的评论",
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
    from app.composition import get_community_moderation_module

    app.dependency_overrides[get_supabase_auth_gateway_factory] = (
        lambda: lambda: FakeAuthGateway()
    )
    app.dependency_overrides[get_community_moderation_module] = lambda: TravelNoteModerationModule(repository)
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def test_admin_queue_and_review_are_authenticated_and_privacy_safe(client: TestClient):
    anonymous = client.get("/api/admin/community/review-queue?target_type=note")
    non_admin = client.get(
        "/api/admin/community/review-queue?target_type=note", headers=_headers("user")
    )
    queue = client.get("/api/admin/community/review-queue?target_type=note", headers=_headers("admin"))
    rejected = client.post(
        f"/api/admin/community/reviews/note/{NOTE_ID}/reject",
        json={"reason": "内容需要补充来源"},
        headers=_headers("admin"),
    )

    assert anonymous.status_code == 401
    assert non_admin.status_code == 403
    assert non_admin.json()["detail"]["code"] == "COMMUNITY_ADMIN_REQUIRED"
    assert queue.status_code == 200
    assert queue.json()["items"][0]["id"] == str(NOTE_ID)
    assert "storage_path" not in queue.text
    assert "author_id" not in queue.text
    assert "email" not in queue.text
    assert rejected.status_code == 200
    assert rejected.json()["status"] == "rejected"


def test_comment_review_and_report_action_are_explicit(client: TestClient):
    approved = client.post(
        f"/api/admin/community/reviews/comment/{COMMENT_ID}/approve",
        json={},
        headers=_headers("admin"),
    )
    actioned = client.post(
        f"/api/admin/community/reports/{REPORT_ID}/resolve",
        json={"decision": "actioned", "resolution_note": "已处理举报"},
        headers=_headers("admin"),
    )

    assert approved.status_code == 200
    assert approved.json()["status"] == "approved"
    assert actioned.status_code == 200
    assert actioned.json()["status"] == "actioned"
    assert actioned.json()["target_id"] == str(COMMENT_ID)

def test_review_queue_exposes_all_three_target_types(client: TestClient):
    for target_type in ("note", "comment", "report"):
        response = client.get(
            f"/api/admin/community/review-queue?target_type={target_type}",
            headers=_headers("admin"),
        )
        assert response.status_code == 200
        assert "next_cursor" in response.json()


def test_admin_hide_content_is_explicit_and_does_not_use_report_actioned(client: TestClient):
    hidden = client.post(
        f"/api/admin/community/hide/note/{NOTE_ID}",
        headers=_headers("admin"),
    )
    assert hidden.status_code == 200
    assert hidden.json()["target_type"] == "note"
    assert hidden.json()["target_id"] == str(NOTE_ID)
    assert hidden.json()["hidden"] is True

    non_admin = client.post(
        f"/api/admin/community/hide/note/{NOTE_ID}",
        headers=_headers("user"),
    )
    assert non_admin.status_code == 403
