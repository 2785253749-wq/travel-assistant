import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest
from pydantic import ValidationError

from app.core.errors import AppError
from app.infrastructure.repositories import InMemoryTripRepository
from app.schemas import Itinerary, TravelProfile
from app.trips.service import TripService


USER_A = UUID("11111111-1111-1111-1111-111111111111")
USER_B = UUID("22222222-2222-2222-2222-222222222222")


@pytest.fixture
def repository():
    return InMemoryTripRepository()


@pytest.fixture
def service(repository):
    return TripService(repository)


@pytest.fixture
def trip(service):
    return service.create_trip(
        USER_A, TravelProfile(origin="Shanghai", destination="Hangzhou", travelers=2)
    )


def test_other_user_cannot_read_trip(service, trip):
    with pytest.raises(AppError) as error:
        service.get_trip(USER_B, trip.id)

    assert error.value.code == "TRIP_NOT_FOUND"


def test_create_trip_uses_authenticated_owner_and_profile_title(service):
    trip = service.create_trip(USER_A, TravelProfile(destination="Hangzhou"))

    assert trip.user_id == USER_A
    assert trip.title == "Hangzhou trip"


def test_trip_titles_are_bounded_for_long_destinations_and_updates(service):
    trip = service.create_trip(USER_A, TravelProfile(destination="x" * 200))

    assert trip.title == f"{'x' * 95} trip"
    assert len(trip.title) == 100

    with pytest.raises(ValidationError):
        service.update_trip(USER_A, trip.id, title="x" * 101)


def test_long_destination_trip_remains_shareable(service):
    trip = service.create_trip(USER_A, TravelProfile(destination="目的地" * 66 + "目的"))

    token = service.create_share_link(USER_A, trip.id)

    assert service.get_shared_trip(token)["title"] == trip.title
    assert len(trip.title) == 100


def test_share_token_is_stored_as_hash(service, repository, trip):
    token = service.create_share_link(USER_A, trip.id)

    stored = repository.last_share_link
    assert stored.token_hash == hashlib.sha256(token.encode("utf-8")).hexdigest()
    assert stored.expires_at > datetime.now(UTC)


def test_revoked_or_expired_share_cannot_be_read(service, repository, trip):
    token = service.create_share_link(USER_A, trip.id)
    service.revoke_share_link(USER_A, trip.id)
    with pytest.raises(AppError) as revoked:
        service.get_shared_trip(token)
    assert revoked.value.code == "SHARE_NOT_FOUND"

    fresh_token = service.create_share_link(USER_A, trip.id, expires_in_days=1)
    repository.last_share_link.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    with pytest.raises(AppError) as expired:
        service.get_shared_trip(fresh_token)
    assert expired.value.code == "SHARE_NOT_FOUND"


def test_public_view_excludes_owner_and_conversation_data(service, trip):
    service.append_message(USER_A, trip.id, role="user", content="private details")
    token = service.create_share_link(USER_A, trip.id)

    shared = service.get_shared_trip(token)

    assert shared == {
        "id": str(trip.id),
        "title": trip.title,
        "status": trip.status,
        "profile": trip.profile.model_dump(mode="json"),
        "itinerary": None,
        "updated_at": trip.updated_at.isoformat(),
    }


def test_server_validated_itinerary_can_be_persisted_and_copied_without_aliasing(service, trip):
    itinerary = Itinerary.model_validate_json(
        Path("tests/fixtures/task7_itinerary.json").read_text(encoding="utf-8")
    )
    planned = service.update_trip(
        USER_A, trip.id, status="planned", itinerary=itinerary
    )

    copied = service.copy_trip(USER_A, trip.id)

    assert planned.itinerary == itinerary
    assert copied.id != planned.id
    assert copied.user_id == USER_A
    assert copied.status == "planned"
    assert copied.itinerary == itinerary
    assert copied.itinerary is not planned.itinerary
    assert copied.title == f"{planned.title} (copy)"


def test_planned_chat_is_saved_as_one_atomic_repository_operation(service, repository):
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

    planned = service.persist_planned_chat(
        USER_A,
        None,
        profile,
        itinerary,
        "请规划成都行程",
        "# 成都行程\n\n可读摘要",
    )

    assert planned.status == "planned"
    assert planned.itinerary == itinerary
    assert [(message.role, message.content) for message in repository.messages] == [
        ("user", "请规划成都行程"),
        ("assistant", "# 成都行程\n\n可读摘要"),
    ]


def test_invalid_planned_chat_message_cannot_partially_create_or_update_trip(service, repository, trip):
    itinerary = Itinerary.model_validate_json(
        Path("tests/fixtures/task7_itinerary.json").read_text(encoding="utf-8")
    )
    original = trip.status, trip.itinerary

    with pytest.raises(ValidationError):
        service.persist_planned_chat(
            USER_A,
            trip,
            trip.profile,
            itinerary,
            "user message",
            "x" * 4001,
        )

    stored = service.get_trip(USER_A, trip.id)
    assert (stored.status, stored.itinerary) == original
    assert repository.messages == []
