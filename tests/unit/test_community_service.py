from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest

from app.core.errors import AppError
from app.community.models import CommunityPost
from app.community.service import CommunityModule, InMemoryCommunityRepository
from app.infrastructure.repositories import InMemoryTripRepository
from app.profile.repositories import InMemoryProfileRepository
from app.schemas import Itinerary, TravelProfile
from app.trips.models import Trip


USER_A = UUID("11111111-1111-1111-1111-111111111111")
USER_B = UUID("22222222-2222-2222-2222-222222222222")


@pytest.fixture
def trip_repository() -> InMemoryTripRepository:
    return InMemoryTripRepository()


@pytest.fixture
def profile_repository() -> InMemoryProfileRepository:
    return InMemoryProfileRepository()


@pytest.fixture
def community_repository(
    trip_repository: InMemoryTripRepository,
    profile_repository: InMemoryProfileRepository,
) -> InMemoryCommunityRepository:
    return InMemoryCommunityRepository(
        trip_repository=trip_repository,
        profile_repository=profile_repository,
    )


@pytest.fixture
def module(community_repository: InMemoryCommunityRepository) -> CommunityModule:
    return CommunityModule(community_repository, community_repository)


def _itinerary() -> Itinerary:
    return Itinerary.model_validate_json(
        Path("tests/fixtures/task7_itinerary.json").read_text(encoding="utf-8")
    )


def _planned_trip(
    trip_repository: InMemoryTripRepository, *, user_id: UUID, destination: str
) -> Trip:
    itinerary = _itinerary()
    trip = Trip(
        user_id=user_id,
        title=f"{destination}行程",
        profile=TravelProfile(origin="上海", destination=destination),
        status="planned",
        itinerary=itinerary,
    )
    return trip_repository.create(trip)


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


def test_publish_owned_planned_trip_uses_public_snapshot_and_author_name(
    module: CommunityModule,
    trip_repository: InMemoryTripRepository,
    profile_repository: InMemoryProfileRepository,
):
    trip = _planned_trip(trip_repository, user_id=USER_A, destination="厦门")
    _seed_profile(profile_repository, user_id=USER_A, display_name="  Voyage Alice  ")

    post = module.publish(USER_A, trip.id, "  海边散步和沙茶面。  ")

    assert post.author_display_name == "Voyage Alice"
    assert post.title == trip.title
    assert post.destination == "厦门"
    assert post.summary == "海边散步和沙茶面。"
    assert post.itinerary_snapshot == trip.itinerary.model_dump(mode="json")
    assert post.can_delete is True


def test_publish_rejects_a_trip_owned_by_another_user(
    module: CommunityModule, trip_repository: InMemoryTripRepository
):
    trip = _planned_trip(trip_repository, user_id=USER_A, destination="杭州")

    with pytest.raises(AppError) as error:
        module.publish(USER_B, trip.id, "想公开这次杭州行程")

    assert error.value.code == "COMMUNITY_POST_NOT_FOUND"


def test_publish_rejects_a_trip_that_is_not_planned(
    module: CommunityModule, trip_repository: InMemoryTripRepository
):
    trip = _collecting_trip(trip_repository, user_id=USER_A, destination="成都")

    with pytest.raises(AppError) as error:
        module.publish(USER_A, trip.id, "还没规划完")

    assert error.value.code == "COMMUNITY_TRIP_NOT_PUBLISHABLE"


def test_publish_deep_copies_the_itinerary_snapshot(
    module: CommunityModule, trip_repository: InMemoryTripRepository
):
    trip = _planned_trip(trip_repository, user_id=USER_A, destination="苏州")

    post = module.publish(USER_A, trip.id, "园林和面馆")
    trip.itinerary.days[0].morning.title = "被修改后的私有行程"
    trip.itinerary.days[0].morning.notes.append("private")

    fetched = module.get_post(post.id, viewer_id=USER_A)

    assert fetched.itinerary_snapshot["days"][0]["morning"]["title"] == "Day 1 morning"
    assert fetched.itinerary_snapshot["days"][0]["morning"]["notes"] == []


def test_publish_rejects_duplicate_publication_for_the_same_source_trip(
    module: CommunityModule, trip_repository: InMemoryTripRepository
):
    trip = _planned_trip(trip_repository, user_id=USER_A, destination="泉州")

    module.publish(USER_A, trip.id, "第一次发布")

    with pytest.raises(AppError) as error:
        module.publish(USER_A, trip.id, "第二次发布")

    assert error.value.code == "COMMUNITY_POST_EXISTS"


def test_list_and_detail_use_opaque_cursor_pagination_and_private_can_delete_derivation(
    module: CommunityModule,
    trip_repository: InMemoryTripRepository,
    profile_repository: InMemoryProfileRepository,
    community_repository: InMemoryCommunityRepository,
):
    _seed_profile(profile_repository, user_id=USER_A, display_name="Alice")
    _seed_profile(profile_repository, user_id=USER_B, display_name="Bob")
    trip_a = _planned_trip(trip_repository, user_id=USER_A, destination="厦门")
    trip_b = _planned_trip(trip_repository, user_id=USER_B, destination="杭州")

    post_a = module.publish(USER_A, trip_a.id, "A 的行程")
    post_b = module.publish(USER_B, trip_b.id, "B 的行程")
    community_repository.posts[post_a.id].post = post_a.model_copy(
        update={
            "created_at": datetime(2026, 8, 20, 8, 0, tzinfo=UTC),
            "updated_at": datetime(2026, 8, 20, 8, 0, tzinfo=UTC),
        }
    )
    community_repository.posts[post_b.id].post = post_b.model_copy(
        update={
            "created_at": datetime(2026, 8, 20, 9, 0, tzinfo=UTC),
            "updated_at": datetime(2026, 8, 20, 9, 0, tzinfo=UTC),
        }
    )

    first_page = module.list_posts(cursor=None, limit=1, viewer_id=USER_A)

    assert [post.id for post in first_page.items] == [post_b.id]
    assert first_page.items[0].can_delete is False
    assert isinstance(first_page.next_cursor, str)

    second_page = module.list_posts(
        cursor=first_page.next_cursor, limit=1, viewer_id=USER_A
    )

    assert [post.id for post in second_page.items] == [post_a.id]
    assert second_page.items[0].can_delete is True
    assert second_page.next_cursor is None

    own_detail = module.get_post(post_a.id, viewer_id=USER_A)
    other_detail = module.get_post(post_b.id, viewer_id=USER_A)

    assert own_detail.can_delete is True
    assert other_detail.can_delete is False


def test_limit_fifty_uses_one_internal_lookahead_row_for_the_next_cursor(
    community_repository: InMemoryCommunityRepository,
):
    created_at = datetime(2026, 8, 20, 12, 0, tzinfo=UTC)
    posts = [
        CommunityPost(
            id=UUID(int=index + 1),
            author_display_name="Voyage 旅行者",
            title=f"行程 {index + 1}",
            destination="厦门",
            summary="公开摘要",
            itinerary_snapshot={"days": []},
            created_at=created_at - timedelta(minutes=index),
            updated_at=created_at - timedelta(minutes=index),
        )
        for index in range(51)
    ]

    class PublicRepository:
        def __init__(self):
            self.requested_limits = []

        def list_posts(self, cursor, limit):
            assert cursor is None
            self.requested_limits.append(limit)
            return posts[:limit]

        def get_post(self, post_id):
            raise AssertionError("detail is not used by this pagination test")

    public_repository = PublicRepository()
    module = CommunityModule(community_repository, public_repository)

    page = module.list_posts(cursor=None, limit=50)

    assert public_repository.requested_limits == [51]
    assert len(page.items) == 50
    assert [post.id for post in page.items] == [post.id for post in posts[:50]]
    assert page.next_cursor is not None


def test_withdraw_rejects_cross_user_requests_and_keeps_the_post_available(
    module: CommunityModule, trip_repository: InMemoryTripRepository
):
    trip = _planned_trip(trip_repository, user_id=USER_A, destination="福州")
    post = module.publish(USER_A, trip.id, "作者自己可以撤下")

    with pytest.raises(AppError) as error:
        module.withdraw(USER_B, post.id)

    assert error.value.code == "COMMUNITY_POST_NOT_FOUND"
    assert module.get_post(post.id, viewer_id=USER_A).id == post.id
