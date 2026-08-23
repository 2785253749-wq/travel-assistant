from pathlib import Path
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from app.api.auth import AuthenticatedUser, get_supabase_auth_gateway_factory
from app.community.service import CommunityModule, InMemoryCommunityRepository
from app.infrastructure.repositories import InMemoryTripRepository
from app.main import app
from app.profile.repositories import InMemoryProfileRepository
from app.schemas import Itinerary, TravelProfile
from app.core.errors import AppError
from app.trips.models import Trip


USER_A = UUID("11111111-1111-1111-1111-111111111111")
USER_B = UUID("22222222-2222-2222-2222-222222222222")


class FakeAuthGateway:
    def get_user(self, token: str) -> AuthenticatedUser:
        if token == "user-a":
            return AuthenticatedUser(id=USER_A, email="alice@example.com")
        if token == "user-b":
            return AuthenticatedUser(id=USER_B, email="bob@example.com")
        raise RuntimeError("unexpected token")


@pytest.fixture
def trip_repository() -> InMemoryTripRepository:
    return InMemoryTripRepository()


@pytest.fixture
def profile_repository() -> InMemoryProfileRepository:
    return InMemoryProfileRepository()


@pytest.fixture
def community_module(
    trip_repository: InMemoryTripRepository, profile_repository: InMemoryProfileRepository
) -> CommunityModule:
    repository = InMemoryCommunityRepository(
        trip_repository=trip_repository, profile_repository=profile_repository
    )
    return CommunityModule(repository, repository)


@pytest.fixture
def client(monkeypatch, community_module: CommunityModule):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    from app.composition import get_community_module, get_optional_community_module
    from app.core.config import get_settings

    get_settings.cache_clear()
    app.dependency_overrides[get_supabase_auth_gateway_factory] = (
        lambda: lambda: FakeAuthGateway()
    )
    app.dependency_overrides[get_community_module] = lambda: community_module
    app.dependency_overrides[get_optional_community_module] = lambda: community_module
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
    get_settings.cache_clear()


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _itinerary() -> Itinerary:
    return Itinerary.model_validate_json(
        Path("tests/fixtures/task7_itinerary.json").read_text(encoding="utf-8")
    )


def _planned_trip(
    trip_repository: InMemoryTripRepository, *, user_id: UUID, destination: str
) -> Trip:
    return trip_repository.create(
        Trip(
            user_id=user_id,
            title=f"{destination}行程",
            profile=TravelProfile(origin="上海", destination=destination),
            status="planned",
            itinerary=_itinerary(),
        )
    )


def _collecting_trip(
    trip_repository: InMemoryTripRepository, *, user_id: UUID, destination: str
) -> Trip:
    return trip_repository.create(
        Trip(
            user_id=user_id,
            title=f"{destination}行程",
            profile=TravelProfile(origin="上海", destination=destination),
        )
    )


def _seed_profile(
    profile_repository: InMemoryProfileRepository, *, user_id: UUID, display_name: str
) -> None:
    profile_repository.replace(
        user_id,
        display_name=display_name,
        preferences={"bio": "", "home_city": "", "travel_styles": []},
        # Task 9 added avatar storage to the profile repository contract.
        avatar_path=None,
    )


def test_anonymous_list_and_detail_are_public_only(
    client: TestClient,
    community_module: CommunityModule,
    trip_repository: InMemoryTripRepository,
    profile_repository: InMemoryProfileRepository,
):
    _seed_profile(profile_repository, user_id=USER_A, display_name="Voyage Alice")
    trip = _planned_trip(trip_repository, user_id=USER_A, destination="厦门")
    post = community_module.publish(USER_A, trip.id, "海边散步和沙茶面。")

    listing = client.get("/api/community/posts")
    detail = client.get(f"/api/community/posts/{post.id}")

    assert listing.status_code == 200
    assert detail.status_code == 200
    assert set(listing.json()) == {"items", "next_cursor"}
    assert len(listing.json()["items"]) == 1
    assert set(detail.json()) == {
        "id",
        "author_display_name",
        "title",
        "destination",
        "summary",
        "itinerary_snapshot",
        "created_at",
        "updated_at",
        "can_delete",
    }
    assert detail.json()["can_delete"] is False
    assert "alice@example.com" not in detail.text
    assert str(USER_A) not in detail.text
    assert "source_trip_id" not in detail.text
    assert "conversations" not in detail.text


def test_cursor_and_limit_validation_use_stable_422_responses(
    client: TestClient,
):
    invalid_cursor = client.get("/api/community/posts", params={"cursor": "not-a-cursor"})
    invalid_limit = client.get("/api/community/posts", params={"limit": 0})
    internal_only_limit = client.get("/api/community/posts", params={"limit": 51})

    assert invalid_cursor.status_code == 422
    assert invalid_cursor.json()["detail"]["code"] == "COMMUNITY_VALIDATION_FAILED"
    assert invalid_limit.status_code == 422
    assert invalid_limit.json()["detail"]["code"] == "REQUEST_INVALID"
    assert internal_only_limit.status_code == 422
    assert internal_only_limit.json()["detail"]["code"] == "REQUEST_INVALID"


def test_authenticated_publish_returns_201_and_a_private_can_delete_flag(
    client: TestClient,
    trip_repository: InMemoryTripRepository,
    profile_repository: InMemoryProfileRepository,
):
    _seed_profile(profile_repository, user_id=USER_A, display_name="Voyage Alice")
    trip = _planned_trip(trip_repository, user_id=USER_A, destination="杭州")

    response = client.post(
        "/api/community/posts",
        headers=_headers("user-a"),
        json={"trip_id": str(trip.id), "summary": "  西湖散步和小吃  "},
    )

    assert response.status_code == 201
    assert response.json()["summary"] == "西湖散步和小吃"
    assert response.json()["can_delete"] is True
    assert "source_trip_id" not in response.text
    assert "user_id" not in response.text
    assert "email" not in response.text


def test_publish_maps_duplicate_and_non_publishable_failures(
    client: TestClient,
    trip_repository: InMemoryTripRepository,
):
    planned = _planned_trip(trip_repository, user_id=USER_A, destination="泉州")
    collecting = _collecting_trip(trip_repository, user_id=USER_A, destination="成都")

    first = client.post(
        "/api/community/posts",
        headers=_headers("user-a"),
        json={"trip_id": str(planned.id), "summary": "第一次发布"},
    )
    duplicate = client.post(
        "/api/community/posts",
        headers=_headers("user-a"),
        json={"trip_id": str(planned.id), "summary": "第二次发布"},
    )
    not_publishable = client.post(
        "/api/community/posts",
        headers=_headers("user-a"),
        json={"trip_id": str(collecting.id), "summary": "还没规划完成"},
    )

    assert first.status_code == 201
    assert duplicate.status_code == 409
    assert duplicate.json()["detail"]["code"] == "COMMUNITY_POST_EXISTS"
    assert not_publishable.status_code == 422
    assert not_publishable.json()["detail"]["code"] == "COMMUNITY_TRIP_NOT_PUBLISHABLE"


def test_delete_is_author_only_and_missing_resources_stay_404(
    client: TestClient,
    community_module: CommunityModule,
    trip_repository: InMemoryTripRepository,
):
    trip = _planned_trip(trip_repository, user_id=USER_A, destination="福州")
    post = community_module.publish(USER_A, trip.id, "作者可以撤下")

    forbidden = client.delete(
        f"/api/community/posts/{post.id}", headers=_headers("user-b")
    )
    deleted = client.delete(
        f"/api/community/posts/{post.id}", headers=_headers("user-a")
    )
    missing = client.delete(
        f"/api/community/posts/{post.id}", headers=_headers("user-a")
    )

    assert forbidden.status_code == 404
    assert forbidden.json()["detail"]["code"] == "COMMUNITY_POST_NOT_FOUND"
    assert deleted.status_code == 204
    assert missing.status_code == 404
    assert missing.json()["detail"]["code"] == "COMMUNITY_POST_NOT_FOUND"


def test_detail_and_list_keep_stable_404s_and_private_can_delete_derivation(
    client: TestClient,
    community_module: CommunityModule,
    trip_repository: InMemoryTripRepository,
):
    trip_a = _planned_trip(trip_repository, user_id=USER_A, destination="南京")
    trip_b = _planned_trip(trip_repository, user_id=USER_B, destination="苏州")
    post_a = community_module.publish(USER_A, trip_a.id, "A 的行程")
    community_module.publish(USER_B, trip_b.id, "B 的行程")

    own_listing = client.get("/api/community/posts", headers=_headers("user-a"))
    own_detail = client.get(
        f"/api/community/posts/{post_a.id}", headers=_headers("user-a")
    )
    missing = client.get(f"/api/community/posts/{UUID(int=0)}")

    assert own_listing.status_code == 200
    assert any(
        item["id"] == str(post_a.id) and item["can_delete"] is True
        for item in own_listing.json()["items"]
    )
    assert own_detail.status_code == 200
    assert own_detail.json()["can_delete"] is True
    assert missing.status_code == 404
    assert missing.json()["detail"]["code"] == "COMMUNITY_POST_NOT_FOUND"


def test_public_list_and_detail_outages_return_stable_503_shape(client: TestClient):
    from app.composition import get_optional_community_module

    class FailingPublicModule:
        def list_posts(self, cursor, limit, viewer_id=None):
            raise AppError("COMMUNITY_PUBLISH_FAILED", "Community publish failed")

        def get_post(self, post_id, viewer_id=None):
            raise AppError("COMMUNITY_PUBLISH_FAILED", "Community publish failed")

    app.dependency_overrides[get_optional_community_module] = lambda: FailingPublicModule()
    try:
        listing = client.get("/api/community/posts")
        detail = client.get(f"/api/community/posts/{UUID(int=1)}")
    finally:
        app.dependency_overrides.pop(get_optional_community_module, None)

    assert listing.status_code == 503
    assert listing.json()["detail"] == {
        "code": "COMMUNITY_PUBLISH_FAILED",
        "message": "Community publish failed",
    }
    assert detail.status_code == 503
    assert detail.json()["detail"] == {
        "code": "COMMUNITY_PUBLISH_FAILED",
        "message": "Community publish failed",
    }


def test_authenticated_list_owned_id_and_delete_outages_return_stable_503_shape(
    client: TestClient,
):
    from app.composition import get_community_module, get_optional_community_module

    class FailingPrivateModule:
        def list_posts(self, cursor, limit, viewer_id=None):
            raise AppError("COMMUNITY_PUBLISH_FAILED", "Community publish failed")

        def withdraw(self, user_id, post_id):
            raise AppError("COMMUNITY_PUBLISH_FAILED", "Community publish failed")

    app.dependency_overrides[get_optional_community_module] = lambda: FailingPrivateModule()
    app.dependency_overrides[get_community_module] = lambda: FailingPrivateModule()
    try:
        listing = client.get("/api/community/posts", headers=_headers("user-a"))
        deleted = client.delete(
            f"/api/community/posts/{UUID(int=2)}", headers=_headers("user-a")
        )
    finally:
        app.dependency_overrides.pop(get_optional_community_module, None)
        app.dependency_overrides.pop(get_community_module, None)

    assert listing.status_code == 503
    assert listing.json()["detail"] == {
        "code": "COMMUNITY_PUBLISH_FAILED",
        "message": "Community publish failed",
    }
    assert deleted.status_code == 503
    assert deleted.json()["detail"] == {
        "code": "COMMUNITY_PUBLISH_FAILED",
        "message": "Community publish failed",
    }
