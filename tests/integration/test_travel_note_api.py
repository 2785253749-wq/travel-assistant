from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from app.api.auth import AuthenticatedUser, get_supabase_auth_gateway_factory
from app.core.errors import AppError
from app.main import app
from app.travel_notes.in_memory import (
    FixedClock,
    InMemoryTravelNoteMediaGateway,
    InMemoryTravelNoteRepository,
)
from app.travel_notes.models import TravelNoteDraftInput
from app.travel_notes.service import TravelNoteModule


USER_A = UUID("11111111-1111-1111-1111-111111111111")
USER_B = UUID("22222222-2222-2222-2222-222222222222")
TRIP_A = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")


class FakeAuthGateway:
    def get_user(self, token: str) -> AuthenticatedUser:
        if token == "user-a":
            return AuthenticatedUser(id=USER_A, email="alice@example.com")
        if token == "user-b":
            return AuthenticatedUser(id=USER_B, email="bob@example.com")
        raise RuntimeError("unexpected token")


def draft_payload(
    *,
    title: str = "大理四天三夜",
    body: str = "苍山脚下散步，傍晚去洱海看日落。",
    location_name: str = "云南·大理",
    category: str = "城市漫步",
    source_trip_id: UUID | None = None,
    image_owner: UUID = USER_A,
) -> dict[str, object]:
    image_token = uuid4()
    return {
        "title": title,
        "body": body,
        "location_name": location_name,
        "category": category,
        "source_trip_id": None if source_trip_id is None else str(source_trip_id),
        "images": [
            {
                "storage_path": f"{image_owner}/{image_token}/cover.webp",
                "sort_order": 0,
                "width": 1440,
                "height": 1920,
            }
        ],
    }


def _draft_input(**overrides: object) -> TravelNoteDraftInput:
    return TravelNoteDraftInput.model_validate(draft_payload(**overrides))


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def travel_note_bundle() -> tuple[TravelNoteModule, InMemoryTravelNoteRepository, FixedClock]:
    repository = InMemoryTravelNoteRepository()
    repository.add_source_trip(
        USER_A,
        TRIP_A,
        {"title": "大理慢游", "days": 4, "highlights": ["苍山", "洱海"]},
    )
    clock = FixedClock(datetime(2026, 8, 21, 9, 0, tzinfo=UTC))
    module = TravelNoteModule(
        repository=repository,
        public_repository=repository,
        media_gateway=InMemoryTravelNoteMediaGateway(),
        clock=clock,
    )
    return module, repository, clock


@pytest.fixture
def client(monkeypatch, travel_note_bundle):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    from app.composition import get_optional_travel_note_module, get_travel_note_module
    from app.core.config import get_settings

    module, _, _ = travel_note_bundle
    get_settings.cache_clear()
    app.dependency_overrides[get_supabase_auth_gateway_factory] = (
        lambda: lambda: FakeAuthGateway()
    )
    app.dependency_overrides[get_travel_note_module] = lambda: module
    app.dependency_overrides[get_optional_travel_note_module] = lambda: module
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
    get_settings.cache_clear()


def _approve_note(
    module: TravelNoteModule, clock: FixedClock, *, title: str, category: str = "城市漫步"
):
    created = module.create_draft(
        USER_A,
        _draft_input(title=title, category=category, source_trip_id=TRIP_A),
    )
    module.submit(USER_A, created.id)
    clock.set(clock.now() + timedelta(minutes=10))
    return module.approve(USER_B, created.id)


def test_anonymous_feed_uses_approved_projection(
    client: TestClient,
    travel_note_bundle: tuple[TravelNoteModule, InMemoryTravelNoteRepository, FixedClock],
):
    module, _, clock = travel_note_bundle
    approved = _approve_note(module, clock, title="大理四天三夜")

    response = client.get("/api/community/notes?limit=20&category=城市漫步&q=大理")

    assert response.status_code == 200
    assert set(response.json()) == {"items", "next_cursor"}
    assert response.json()["next_cursor"] is None
    assert len(response.json()["items"]) == 1
    item = response.json()["items"][0]
    assert item["id"] == str(approved.id)
    assert item["title"] == "大理四天三夜"
    assert "source_trip_id" not in item
    assert "author_id" not in item
    assert "storage_path" not in response.text
    assert "review_reason" not in response.text

def test_public_detail_and_creator_projection_exclude_private_fields(
    client: TestClient,
    travel_note_bundle: tuple[TravelNoteModule, InMemoryTravelNoteRepository, FixedClock],
):
    module, _, clock = travel_note_bundle
    approved = _approve_note(module, clock, title="创作者公开游记")

    detail = client.get(f"/api/community/notes/{approved.id}")
    creator = client.get("/api/community/creators/voyage-traveler")

    assert detail.status_code == 200
    assert creator.status_code == 200
    assert detail.json()["author_slug"] == "voyage-traveler"
    assert creator.json()["creator"]["creator_slug"] == "voyage-traveler"
    assert creator.json()["items"][0]["id"] == str(approved.id)
    for response in (detail, creator):
        for forbidden in ("author_id", "email", "source_trip_id", "storage_path", "review_reason"):
            assert forbidden not in response.text


def test_owner_draft_lifecycle_requires_authentication(client: TestClient):
    anonymous = client.post("/api/community/notes", json=draft_payload(source_trip_id=TRIP_A))
    created = client.post(
        "/api/community/notes",
        json=draft_payload(source_trip_id=TRIP_A),
        headers=_headers("user-a"),
    )
    updated = client.put(
        f"/api/community/notes/{created.json()['id']}",
        json=draft_payload(title="重新整理后的标题", source_trip_id=TRIP_A),
        headers=_headers("user-a"),
    )
    submitted = client.post(
        f"/api/community/notes/{created.json()['id']}/submit",
        headers=_headers("user-a"),
    )

    assert anonymous.status_code == 401
    assert anonymous.json()["detail"]["code"] == "AUTH_REQUIRED"
    assert created.status_code == 201
    assert created.json()["status"] == "draft"
    assert updated.status_code == 200
    assert updated.json()["title"] == "重新整理后的标题"
    assert submitted.status_code == 200
    assert submitted.json()["status"] == "pending_review"
    assert "author_id" not in created.text
    assert "storage_path" in created.text


def test_mine_filters_by_status_and_delete_soft_removes_owned_notes(
    client: TestClient,
    travel_note_bundle: tuple[TravelNoteModule, InMemoryTravelNoteRepository, FixedClock],
):
    module, _, _ = travel_note_bundle
    draft = module.create_draft(USER_A, _draft_input(source_trip_id=TRIP_A))
    pending = module.create_draft(
        USER_A,
        _draft_input(title="待审核游记", category="自然风光", source_trip_id=TRIP_A),
    )
    module.submit(USER_A, pending.id)

    draft_listing = client.get("/api/me/travel-notes?status=draft", headers=_headers("user-a"))
    pending_listing = client.get(
        "/api/me/travel-notes?status=pending_review", headers=_headers("user-a")
    )
    deleted = client.delete(f"/api/community/notes/{draft.id}", headers=_headers("user-a"))
    after_delete = client.get("/api/me/travel-notes?status=draft", headers=_headers("user-a"))

    assert draft_listing.status_code == 200
    assert [item["id"] for item in draft_listing.json()["items"]] == [str(draft.id)]
    assert pending_listing.status_code == 200
    assert [item["id"] for item in pending_listing.json()["items"]] == [str(pending.id)]
    assert deleted.status_code == 204
    assert after_delete.json()["items"] == []


def test_invalid_cursor_category_status_and_cross_owner_paths_use_stable_errors(
    client: TestClient,
    travel_note_bundle: tuple[TravelNoteModule, InMemoryTravelNoteRepository, FixedClock],
):
    module, _, _ = travel_note_bundle
    created = module.create_draft(USER_A, _draft_input(source_trip_id=TRIP_A))

    invalid_cursor = client.get("/api/community/notes?cursor=not-a-cursor")
    invalid_category = client.get("/api/community/notes?category=随便逛逛")
    invalid_status = client.get("/api/me/travel-notes?status=published", headers=_headers("user-a"))
    forbidden_update = client.put(
        f"/api/community/notes/{created.id}",
        json=draft_payload(image_owner=USER_B, source_trip_id=TRIP_A),
        headers=_headers("user-a"),
    )

    assert invalid_cursor.status_code == 422
    assert invalid_cursor.json()["detail"]["code"] == "TRAVEL_NOTE_VALIDATION_FAILED"
    assert invalid_category.status_code == 422
    assert invalid_category.json()["detail"]["code"] == "TRAVEL_NOTE_VALIDATION_FAILED"
    assert invalid_status.status_code == 422
    assert invalid_status.json()["detail"]["code"] == "TRAVEL_NOTE_VALIDATION_FAILED"
    assert forbidden_update.status_code == 422
    assert forbidden_update.json()["detail"]["code"] == "TRAVEL_NOTE_VALIDATION_FAILED"


def test_invalid_state_and_missing_resources_map_to_409_and_404(
    client: TestClient,
    travel_note_bundle: tuple[TravelNoteModule, InMemoryTravelNoteRepository, FixedClock],
):
    module, _, _ = travel_note_bundle
    created = module.create_draft(USER_A, _draft_input(source_trip_id=TRIP_A))

    first_submit = client.post(
        f"/api/community/notes/{created.id}/submit",
        headers=_headers("user-a"),
    )
    duplicate_submit = client.post(
        f"/api/community/notes/{created.id}/submit",
        headers=_headers("user-a"),
    )
    missing_detail = client.get(f"/api/community/notes/{UUID(int=0)}")
    cross_owner_delete = client.delete(
        f"/api/community/notes/{created.id}",
        headers=_headers("user-b"),
    )

    assert first_submit.status_code == 200
    assert duplicate_submit.status_code == 409
    assert duplicate_submit.json()["detail"]["code"] == "TRAVEL_NOTE_INVALID_STATE"
    assert missing_detail.status_code == 404
    assert missing_detail.json()["detail"]["code"] == "TRAVEL_NOTE_NOT_FOUND"
    assert cross_owner_delete.status_code == 404
    assert cross_owner_delete.json()["detail"]["code"] == "TRAVEL_NOTE_NOT_FOUND"


def test_outages_return_stable_503_shape(client: TestClient):
    from app.composition import get_optional_travel_note_module

    class FailingTravelNoteModule:
        def list_public(self, *, cursor, limit, category=None, search_query=None):
            del cursor, limit, category, search_query
            raise AppError("TRAVEL_NOTE_UNAVAILABLE", "Travel note service is unavailable")

    app.dependency_overrides[get_optional_travel_note_module] = lambda: FailingTravelNoteModule()
    try:
        response = client.get("/api/community/notes")
    finally:
        app.dependency_overrides.pop(get_optional_travel_note_module, None)

    assert response.status_code == 503
    assert response.json()["detail"] == {
        "code": "TRAVEL_NOTE_UNAVAILABLE",
        "message": "Travel note service is unavailable",
    }
