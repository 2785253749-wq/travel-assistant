from __future__ import annotations

from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from app.api.auth import AuthenticatedUser, get_supabase_auth_gateway_factory
from app.main import app
from app.travel_notes.interactions import (
    InMemoryInteractionRepository,
    TravelNoteInteractionModule,
)


USER_A = UUID("11111111-1111-1111-1111-111111111111")
NOTE_ID = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")


class FakeAuthGateway:
    def get_user(self, token: str) -> AuthenticatedUser:
        if token == "user-a":
            return AuthenticatedUser(id=USER_A, email="alice@example.com")
        raise RuntimeError("unexpected token")


def headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    from app.composition import (
        get_optional_travel_note_interaction_module,
        get_travel_note_interaction_module,
    )

    module = TravelNoteInteractionModule(
        InMemoryInteractionRepository(approved_note_ids={NOTE_ID})
    )
    app.dependency_overrides[get_supabase_auth_gateway_factory] = (
        lambda: lambda: FakeAuthGateway()
    )
    app.dependency_overrides[get_travel_note_interaction_module] = lambda: module
    app.dependency_overrides[get_optional_travel_note_interaction_module] = (
        lambda: module
    )
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def test_interactions_require_auth_and_like_bookmark_are_idempotent(client: TestClient):
    anonymous = client.put(f"/api/community/notes/{NOTE_ID}/like")
    first_like = client.put(
        f"/api/community/notes/{NOTE_ID}/like", headers=headers("user-a")
    )
    second_like = client.put(
        f"/api/community/notes/{NOTE_ID}/like", headers=headers("user-a")
    )
    first_bookmark = client.put(
        f"/api/community/notes/{NOTE_ID}/bookmark", headers=headers("user-a")
    )

    assert anonymous.status_code == 401
    assert first_like.status_code == 200
    assert second_like.json()["like_count"] == 1
    assert first_bookmark.json()["bookmarked"] is True
    assert "user_id" not in first_bookmark.text
    assert "email" not in first_bookmark.text


def test_comment_is_pending_and_public_list_hides_it(client: TestClient):
    created = client.post(
        f"/api/community/notes/{NOTE_ID}/comments",
        json={"body": "请问最佳拍摄时间？"},
        headers=headers("user-a"),
    )
    public_comments = client.get(f"/api/community/notes/{NOTE_ID}/comments")
    own_comments = client.get(
        f"/api/community/notes/{NOTE_ID}/comments", headers=headers("user-a")
    )

    assert created.status_code == 201
    assert created.json()["status"] == "pending_review"
    assert public_comments.status_code == 200
    assert public_comments.json()["items"] == []
    assert own_comments.status_code == 200
    assert own_comments.json()["items"][0]["status"] == "pending_review"
    assert own_comments.json()["items"][0]["id"] == created.json()["id"]
    assert "author_id" not in created.text
    assert "review_reason" not in created.text


def test_report_uses_path_note_and_target_type(client: TestClient):
    response = client.post(
        f"/api/community/notes/{NOTE_ID}/reports",
        json={
            "target_type": "note",
            "target_id": str(NOTE_ID),
            "reason": "内容不实",
        },
        headers=headers("user-a"),
    )

    assert response.status_code == 201
    assert response.json()["status"] == "pending"
    assert "reporter_id" not in response.text
    assert "email" not in response.text
