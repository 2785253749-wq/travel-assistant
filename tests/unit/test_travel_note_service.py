from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from app.core.errors import AppError
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
TRIP_B = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")


def draft_input(
    *,
    title: str = "大理四天三夜",
    body: str = "苍山脚下散步，傍晚去洱海看日落。",
    location_name: str = "云南·大理",
    category: str = "城市漫步",
    source_trip_id: UUID | None = None,
    image_owner: UUID = USER_A,
    image_names: tuple[str, ...] = ("cover.webp",),
) -> TravelNoteDraftInput:
    return TravelNoteDraftInput.model_validate(
        {
            "title": title,
            "body": body,
            "location_name": location_name,
            "category": category,
            "source_trip_id": source_trip_id,
            "images": [
                {
                    "storage_path": f"{image_owner}/{uuid4()}/{image_name}",
                    "sort_order": index,
                    "width": 1440 + index,
                    "height": 1920 - index,
                }
                for index, image_name in enumerate(image_names)
            ],
        }
    )


def create_module(
    *,
    clock_now: datetime | None = None,
    repository: InMemoryTravelNoteRepository | None = None,
    public_repository: InMemoryTravelNoteRepository | None = None,
    media_gateway: InMemoryTravelNoteMediaGateway | None = None,
) -> tuple[TravelNoteModule, InMemoryTravelNoteRepository, FixedClock]:
    repository = repository or InMemoryTravelNoteRepository()
    repository.add_source_trip(
        USER_A,
        TRIP_A,
        {
            "title": "大理慢游",
            "days": 4,
            "highlights": ["苍山", "洱海"],
        },
    )
    repository.add_source_trip(
        USER_B,
        TRIP_B,
        {
            "title": "厦门周末",
            "days": 2,
            "highlights": ["鼓浪屿"],
        },
    )
    clock = FixedClock(clock_now or datetime(2026, 8, 21, 9, 0, tzinfo=UTC))
    module = TravelNoteModule(
        repository=repository,
        public_repository=public_repository or repository,
        media_gateway=media_gateway or InMemoryTravelNoteMediaGateway(),
        clock=clock,
    )
    return module, repository, clock


def error_code(exc_info: pytest.ExceptionInfo[AppError]) -> str:
    return exc_info.value.code


class ExplodingMediaGateway(InMemoryTravelNoteMediaGateway):
    def sign_paths(self, paths: list[str]) -> list[str]:
        del paths
        raise RuntimeError("storage offline")


class ShortMediaGateway(InMemoryTravelNoteMediaGateway):
    def sign_paths(self, paths: list[str]) -> list[str]:
        if not paths:
            return []
        return [f"https://signed.example.test/{paths[0]}"]


def test_author_can_create_replace_and_submit_a_complete_draft():
    module, repository, clock = create_module()

    created = module.create_draft(USER_A, draft_input(source_trip_id=TRIP_A))
    clock.set(clock.now() + timedelta(minutes=5))
    replaced = module.replace_draft(
        USER_A,
        created.id,
        draft_input(
            title="新的标题",
            source_trip_id=TRIP_A,
            image_names=("cover.webp", "detail.webp"),
        ),
    )
    clock.set(clock.now() + timedelta(minutes=5))
    submitted = module.submit(USER_A, created.id)

    assert created.status == "draft"
    assert replaced.title == "新的标题"
    assert replaced.images[1].sort_order == 1
    assert submitted.status == "pending_review"
    assert submitted.submitted_at == clock.now()

    stored = repository.get_stored_note(created.id)
    assert stored is not None
    assert stored.source_trip_id == TRIP_A
    assert stored.itinerary_snapshot == {
        "title": "大理慢游",
        "days": 4,
        "highlights": ["苍山", "洱海"],
    }


def test_author_can_attach_and_remove_owned_images_without_replacing_draft():
    module, _, clock = create_module()

    created = module.create_draft(USER_A, draft_input())
    new_image = draft_input(image_names=("cover.webp", "detail.webp")).images[1]
    clock.set(clock.now() + timedelta(minutes=5))
    attached = module.attach_image(USER_A, created.id, new_image)
    clock.set(clock.now() + timedelta(minutes=5))
    removed = module.remove_image(USER_A, created.id, attached.images[1].id)

    assert [image.sort_order for image in attached.images] == [0, 1]
    assert attached.images[1].storage_path == new_image.storage_path
    assert len(removed.images) == 1
    assert removed.images[0].sort_order == 0


def test_remove_image_requires_at_least_one_remaining_image():
    module, _, _ = create_module()
    created = module.create_draft(USER_A, draft_input())

    with pytest.raises(AppError) as error:
        module.remove_image(USER_A, created.id, created.images[0].id)

    assert error_code(error) == "TRAVEL_NOTE_VALIDATION_FAILED"


def test_cross_user_mutation_is_indistinguishable_from_missing():
    module, _, _ = create_module()
    created = module.create_draft(USER_A, draft_input())

    with pytest.raises(AppError) as error:
        module.replace_draft(USER_B, created.id, draft_input(image_owner=USER_B))

    assert error_code(error) == "TRAVEL_NOTE_NOT_FOUND"


def test_create_rejects_images_outside_owner_prefix():
    module, _, _ = create_module()

    with pytest.raises(AppError) as error:
        module.create_draft(USER_A, draft_input(image_owner=USER_B))

    assert error_code(error) == "TRAVEL_NOTE_VALIDATION_FAILED"


def test_create_rejects_source_trip_that_user_does_not_own():
    module, _, _ = create_module()

    with pytest.raises(AppError) as error:
        module.create_draft(USER_A, draft_input(source_trip_id=TRIP_B))

    assert error_code(error) == "TRAVEL_NOTE_NOT_FOUND"


def test_pending_and_approved_notes_are_immutable_to_authors():
    module, _, clock = create_module()
    created = module.create_draft(USER_A, draft_input())
    submitted = module.submit(USER_A, created.id)

    with pytest.raises(AppError) as replace_error:
        module.replace_draft(USER_A, submitted.id, draft_input(title="改标题"))

    with pytest.raises(AppError) as submit_error:
        module.submit(USER_A, submitted.id)

    assert error_code(replace_error) == "TRAVEL_NOTE_INVALID_STATE"
    assert error_code(submit_error) == "TRAVEL_NOTE_INVALID_STATE"

    clock.set(clock.now() + timedelta(hours=1))
    approved = module.approve(USER_B, submitted.id)

    with pytest.raises(AppError) as approved_error:
        module.replace_draft(USER_A, approved.id, draft_input(title="再改一次"))

    assert approved.status == "approved"
    assert error_code(approved_error) == "TRAVEL_NOTE_INVALID_STATE"


def test_rejected_note_can_be_replaced_and_resubmitted():
    module, _, clock = create_module()
    created = module.create_draft(USER_A, draft_input())
    submitted = module.submit(USER_A, created.id)
    clock.set(clock.now() + timedelta(minutes=30))
    rejected = module.reject(USER_B, submitted.id, "  图片与正文主题不一致  ")

    clock.set(clock.now() + timedelta(minutes=10))
    replaced = module.replace_draft(
        USER_A,
        rejected.id,
        draft_input(title="重新整理后的标题", image_names=("cover.webp", "detail.webp")),
    )
    clock.set(clock.now() + timedelta(minutes=10))
    resubmitted = module.submit(USER_A, rejected.id)

    assert rejected.status == "rejected"
    assert rejected.review_reason == "图片与正文主题不一致"
    assert replaced.status == "rejected"
    assert replaced.title == "重新整理后的标题"
    assert resubmitted.status == "pending_review"
    assert resubmitted.review_reason is None


def test_soft_delete_hides_note_from_owner_and_public_views():
    module, _, clock = create_module()
    created = module.create_draft(USER_A, draft_input())
    submitted = module.submit(USER_A, created.id)
    clock.set(clock.now() + timedelta(minutes=15))
    approved = module.approve(USER_B, submitted.id)

    module.soft_delete(USER_A, approved.id)

    assert module.list_mine(USER_A) == []
    assert module.list_public(cursor=None, limit=10).items == []

    with pytest.raises(AppError) as public_error:
        module.get_public(approved.id)

    assert error_code(public_error) == "TRAVEL_NOTE_NOT_FOUND"


def test_list_public_supports_cursor_category_and_normalized_search():
    module, _, clock = create_module()

    first = module.create_draft(
        USER_A,
        draft_input(
            title="大理清晨",
            location_name="云南·大理",
            category="城市漫步",
            image_names=("cover.webp", "detail.webp"),
        ),
    )
    module.submit(USER_A, first.id)
    clock.set(clock.now() + timedelta(hours=1))
    module.approve(USER_B, first.id)

    second = module.create_draft(
        USER_A,
        draft_input(
            title="厦门海风",
            location_name="福建·厦门",
            category="美食地图",
        ),
    )
    module.submit(USER_A, second.id)
    clock.set(clock.now() + timedelta(hours=1))
    module.approve(USER_B, second.id)

    third = module.create_draft(
        USER_A,
        draft_input(
            title="大理夜色",
            location_name="云南·大理",
            category="自然风光",
        ),
    )
    module.submit(USER_A, third.id)
    clock.set(clock.now() + timedelta(hours=1))
    module.approve(USER_B, third.id)

    first_page = module.list_public(cursor=None, limit=2)
    second_page = module.list_public(cursor=first_page.next_cursor, limit=2)
    dali_page = module.list_public(cursor=None, limit=10, search_query="  大理  ")
    food_page = module.list_public(cursor=None, limit=10, category="美食地图")

    assert [item.title for item in first_page.items] == ["大理夜色", "厦门海风"]
    assert first_page.next_cursor is not None
    assert [item.title for item in second_page.items] == ["大理清晨"]
    assert [item.title for item in dali_page.items] == ["大理夜色", "大理清晨"]
    assert [item.title for item in food_page.items] == ["厦门海风"]


def test_list_public_rejects_invalid_limit_and_cursor():
    module, _, _ = create_module()

    with pytest.raises(AppError) as zero_limit:
        module.list_public(cursor=None, limit=0)
    with pytest.raises(AppError) as large_limit:
        module.list_public(cursor=None, limit=51)
    with pytest.raises(AppError) as invalid_cursor:
        module.list_public(cursor="bad-cursor", limit=10)

    assert error_code(zero_limit) == "TRAVEL_NOTE_VALIDATION_FAILED"
    assert error_code(large_limit) == "TRAVEL_NOTE_VALIDATION_FAILED"
    assert error_code(invalid_cursor) == "TRAVEL_NOTE_VALIDATION_FAILED"


def test_get_public_projection_hides_private_fields_but_keeps_public_media():
    module, repository, clock = create_module()
    created = module.create_draft(
        USER_A,
        draft_input(
            source_trip_id=TRIP_A,
            image_names=("cover.webp", "detail.webp"),
        ),
    )
    module.submit(USER_A, created.id)
    clock.set(clock.now() + timedelta(minutes=20))
    module.approve(USER_B, created.id)

    detail = module.get_public(created.id)

    dumped = detail.model_dump()
    assert dumped["cover_image_url"].startswith("https://signed.example.test/")
    assert dumped["images"][0]["image_url"].startswith("https://signed.example.test/")
    assert "source_trip_id" not in dumped
    assert "storage_path" not in dumped["images"][0]

    stored = repository.get_stored_note(created.id)
    assert stored is not None
    assert stored.source_trip_id == TRIP_A
    assert stored.itinerary_snapshot is not None


def test_module_requires_explicit_public_repository_and_media_gateway():
    repository = InMemoryTravelNoteRepository()
    clock = FixedClock(datetime(2026, 8, 21, 9, 0, tzinfo=UTC))

    with pytest.raises(TypeError):
        TravelNoteModule(repository=repository, clock=clock)  # type: ignore[call-arg]


def test_media_signing_failures_and_length_mismatches_return_stable_errors():
    base_module, repository, clock = create_module()
    created = base_module.create_draft(
        USER_A, draft_input(image_names=("cover.webp", "detail.webp"))
    )
    base_module.submit(USER_A, created.id)
    clock.set(clock.now() + timedelta(minutes=20))
    base_module.approve(USER_B, created.id)

    failing_module, _, _ = create_module(
        repository=repository,
        public_repository=repository,
        media_gateway=ExplodingMediaGateway(),
        clock_now=clock.now(),
    )
    with pytest.raises(AppError) as signing_error:
        failing_module.get_public(created.id)

    assert error_code(signing_error) == "TRAVEL_NOTE_UNAVAILABLE"

    mismatch_base_module, mismatch_repository, mismatch_clock = create_module()
    mismatch_created = mismatch_base_module.create_draft(
        USER_A, draft_input(image_names=("cover.webp", "detail.webp"))
    )
    mismatch_base_module.submit(USER_A, mismatch_created.id)
    mismatch_clock.set(mismatch_clock.now() + timedelta(minutes=20))
    mismatch_base_module.approve(USER_B, mismatch_created.id)

    mismatch_module, _, _ = create_module(
        repository=mismatch_repository,
        public_repository=mismatch_repository,
        media_gateway=ShortMediaGateway(),
        clock_now=mismatch_clock.now(),
    )
    with pytest.raises(AppError) as mismatch_error:
        mismatch_module.get_public(mismatch_created.id)

    assert error_code(mismatch_error) == "TRAVEL_NOTE_UNAVAILABLE"


def test_reject_requires_pending_state_and_trimmed_reason():
    module, _, clock = create_module()
    created = module.create_draft(USER_A, draft_input())

    with pytest.raises(AppError) as draft_error:
        module.reject(USER_B, created.id, "需要更多图片")

    assert error_code(draft_error) == "TRAVEL_NOTE_INVALID_STATE"

    submitted = module.submit(USER_A, created.id)
    clock.set(clock.now() + timedelta(minutes=5))
    rejected = module.reject(USER_B, submitted.id, "  需要更多图片  ")

    with pytest.raises(AppError) as blank_error:
        module.reject(USER_B, submitted.id, "   ")

    assert rejected.review_reason == "需要更多图片"
    assert error_code(blank_error) == "TRAVEL_NOTE_INVALID_STATE"
