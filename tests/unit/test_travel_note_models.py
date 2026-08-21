from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from app.travel_notes.models import (
    TravelNoteCard,
    TravelNoteComment,
    TravelNoteDetail,
    TravelNoteDraftInput,
    TravelNoteOwnerView,
    TravelNotePage,
    decode_travel_note_cursor,
    encode_travel_note_cursor,
)


def draft_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "title": "  大理四天三夜  ",
        "body": "  苍山脚下散步，傍晚去洱海看日落。  ",
        "location_name": "  云南·大理  ",
        "category": "城市漫步",
        "source_trip_id": None,
        "images": [
            {
                "storage_path": "user-a/note-a/cover.webp",
                "sort_order": 0,
                "width": 1440,
                "height": 1920,
            }
        ],
    }
    payload.update(overrides)
    return payload


def public_card_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "id": str(uuid4()),
        "title": "大理四天三夜",
        "body_preview": "苍山脚下散步，傍晚去洱海看日落。",
        "location_name": "云南·大理",
        "category": "城市漫步",
        "cover_image_url": "https://cdn.example.test/cover.webp",
        "author_display_name": "Voyage Alice",
        "author_avatar_url": "https://cdn.example.test/avatar.webp",
        "published_at": datetime(2026, 8, 21, 9, 30, tzinfo=UTC),
        "like_count": 12,
        "comment_count": 3,
    }
    payload.update(overrides)
    return payload


def test_draft_requires_one_category_and_one_to_nine_owned_images():
    valid = draft_payload()

    assert TravelNoteDraftInput.model_validate(valid).category == "城市漫步"

    with pytest.raises(ValidationError):
        TravelNoteDraftInput.model_validate({**valid, "images": []})

    with pytest.raises(ValidationError):
        TravelNoteDraftInput.model_validate({**valid, "category": "随便逛逛"})


def test_draft_trims_text_and_rejects_duplicate_image_sort_orders():
    draft = TravelNoteDraftInput.model_validate(draft_payload())

    assert draft.title == "大理四天三夜"
    assert draft.body == "苍山脚下散步，傍晚去洱海看日落。"
    assert draft.location_name == "云南·大理"

    with pytest.raises(ValidationError):
        TravelNoteDraftInput.model_validate(
            draft_payload(
                images=[
                    {
                        "storage_path": "user-a/note-a/cover.webp",
                        "sort_order": 0,
                        "width": 1440,
                        "height": 1920,
                    },
                    {
                        "storage_path": "user-a/note-a/detail.webp",
                        "sort_order": 0,
                        "width": 1920,
                        "height": 1440,
                    },
                ]
            )
        )


def test_draft_rejects_invalid_image_metadata():
    with pytest.raises(ValidationError):
        TravelNoteDraftInput.model_validate(
            draft_payload(
                images=[
                    {
                        "storage_path": "x",
                        "sort_order": 0,
                        "width": 0,
                        "height": 1920,
                    }
                ]
            )
        )


def test_public_card_forbids_private_fields():
    payload = public_card_payload()
    card = TravelNoteCard.model_validate(payload)
    assert card.title == "大理四天三夜"

    for field in ("author_id", "source_trip_id", "storage_path", "review_reason"):
        with pytest.raises(ValidationError):
            TravelNoteCard.model_validate({**payload, field: "private"})


def test_detail_owner_and_page_models_keep_public_contracts_strict():
    card = TravelNoteCard.model_validate(public_card_payload())
    detail = TravelNoteDetail.model_validate(
        {
            **{
                key: value
                for key, value in public_card_payload().items()
                if key != "body_preview"
            },
            "images": [
                {
                    "id": str(uuid4()),
                    "image_url": "https://cdn.example.test/cover.webp",
                    "sort_order": 0,
                    "width": 1440,
                    "height": 1920,
                }
            ],
            "author_slug": "voyage-alice",
            "body": "苍山脚下散步，傍晚去洱海看日落。",
        }
    )
    owner_view = TravelNoteOwnerView.model_validate(
        {
            **{
                key: value
                for key, value in public_card_payload().items()
                if key != "body_preview"
            },
            "body": "苍山脚下散步，傍晚去洱海看日落。",
            "status": "draft",
            "review_reason": None,
            "source_trip_id": None,
            "submitted_at": None,
            "updated_at": datetime(2026, 8, 21, 9, 30, tzinfo=UTC),
            "deleted_at": None,
            "images": [
                {
                    "id": str(uuid4()),
                    "storage_path": "user-a/note-a/cover.webp",
                    "sort_order": 0,
                    "width": 1440,
                    "height": 1920,
                }
            ],
        }
    )
    page = TravelNotePage.model_validate({"items": [card], "next_cursor": None})

    assert detail.title == "大理四天三夜"
    assert owner_view.status == "draft"
    assert page.items[0].title == "大理四天三夜"


def test_travel_note_comment_and_cursor_round_trip():
    comment = TravelNoteComment.model_validate(
        {
            "id": str(uuid4()),
            "note_id": str(uuid4()),
            "author_display_name": "Voyage Alice",
            "body": "请问最佳拍摄时间？",
            "status": "approved",
            "published_at": datetime(2026, 8, 21, 10, 15, tzinfo=UTC),
        }
    )
    assert comment.body == "请问最佳拍摄时间？"

    published_at = datetime(2026, 8, 21, 9, 30, 45, 123456, tzinfo=UTC)
    note_id = UUID("12345678-1234-5678-1234-567812345678")

    cursor = encode_travel_note_cursor(published_at, note_id)

    assert decode_travel_note_cursor(cursor) == (published_at, note_id)

    with pytest.raises(ValueError):
        decode_travel_note_cursor("not-a-valid-cursor")
