from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

import app.community.models as community_models

from app.community.models import CommunityPage, CommunityPost, CommunityPublishInput, CommunityPublishResult, decode_community_cursor, encode_community_cursor


def test_community_post_rejects_out_of_bounds_text_fields():
    with pytest.raises(ValidationError):
        CommunityPost.model_validate(
            {
                "id": str(uuid4()),
                "author_display_name": "Alice",
                "title": "x" * 101,
                "destination": "厦门",
                "summary": "摘要",
                "itinerary_snapshot": {},
                "created_at": datetime(2026, 8, 20, tzinfo=UTC),
                "updated_at": datetime(2026, 8, 20, tzinfo=UTC),
                "can_delete": False,
            }
        )

    with pytest.raises(ValidationError):
        CommunityPost.model_validate(
            {
                "id": str(uuid4()),
                "author_display_name": "Alice",
                "title": "厦门两日游",
                "destination": "x" * 81,
                "summary": "摘要",
                "itinerary_snapshot": {},
                "created_at": datetime(2026, 8, 20, tzinfo=UTC),
                "updated_at": datetime(2026, 8, 20, tzinfo=UTC),
                "can_delete": False,
            }
        )

    with pytest.raises(ValidationError):
        CommunityPost.model_validate(
            {
                "id": str(uuid4()),
                "author_display_name": "Alice",
                "title": "厦门两日游",
                "destination": "厦门",
                "summary": "x" * 301,
                "itinerary_snapshot": {},
                "created_at": datetime(2026, 8, 20, tzinfo=UTC),
                "updated_at": datetime(2026, 8, 20, tzinfo=UTC),
                "can_delete": False,
            }
        )


def test_community_post_requires_snapshot_json_object():
    with pytest.raises(ValidationError):
        CommunityPost.model_validate(
            {
                "id": str(uuid4()),
                "author_display_name": "Alice",
                "title": "厦门两日游",
                "destination": "厦门",
                "summary": "摘要",
                "itinerary_snapshot": [],
                "created_at": datetime(2026, 8, 20, tzinfo=UTC),
                "updated_at": datetime(2026, 8, 20, tzinfo=UTC),
                "can_delete": False,
            }
        )

    with pytest.raises(ValidationError):
        CommunityPost.model_validate(
            {
                "id": str(uuid4()),
                "author_display_name": "Alice",
                "title": "厦门两日游",
                "destination": "厦门",
                "summary": "摘要",
                "itinerary_snapshot": {"days": [{"published_at": datetime(2026, 8, 20, tzinfo=UTC)}]},
                "created_at": datetime(2026, 8, 20, tzinfo=UTC),
                "updated_at": datetime(2026, 8, 20, tzinfo=UTC),
                "can_delete": False,
            }
        )


def test_community_cursor_round_trips_with_stable_encoding():
    created_at = datetime(2026, 8, 20, 9, 30, 45, 123456, tzinfo=UTC)
    post_id = UUID("12345678-1234-5678-1234-567812345678")

    cursor = encode_community_cursor(created_at, post_id)

    assert cursor == "MjAyNi0wOC0yMFQwOTozMDo0NS4xMjM0NTYrMDA6MDB8MTIzNDU2NzgtMTIzNC01Njc4LTEyMzQtNTY3ODEyMzQ1Njc4"
    assert decode_community_cursor(cursor) == (created_at, post_id)


def test_community_page_requires_valid_cursor_shape():
    with pytest.raises(ValueError):
        decode_community_cursor("not-a-valid-cursor")


def test_community_public_models_forbid_private_fields():
    public_payload = {
        "id": str(uuid4()),
        "author_display_name": "Alice",
        "title": "厦门两日游",
        "destination": "厦门",
        "summary": "海边散步和沙茶面。",
        "itinerary_snapshot": {"days": []},
        "created_at": datetime(2026, 8, 20, tzinfo=UTC),
        "updated_at": datetime(2026, 8, 20, tzinfo=UTC),
        "can_delete": False,
    }

    assert CommunityPost.model_validate(public_payload).title == "厦门两日游"
    assert CommunityPage(items=[CommunityPost.model_validate(public_payload)], next_cursor=None)
    assert CommunityPublishInput(trip_id=uuid4(), summary="准备发布")
    assert CommunityPublishResult.model_validate({"post": public_payload})

    for field_name, value in [
        ("user_id", str(uuid4())),
        ("source_trip_id", str(uuid4())),
        ("email", "alice@example.com"),
        ("conversations", ["private"]),
        ("share_token", "secret"),
        ("profile", {"budget_cny": 3000}),
    ]:
        with pytest.raises(ValidationError):
            CommunityPost.model_validate({**public_payload, field_name: value})


def test_community_publish_error_contracts_are_strict_and_use_stable_codes():
    not_found = community_models.CommunityErrorResponse.model_validate(
        {
            "detail": {
                "code": "COMMUNITY_POST_NOT_FOUND",
                "message": "Community post not found",
            }
        }
    )
    assert not_found.detail.code == "COMMUNITY_POST_NOT_FOUND"

    for code in [
        "COMMUNITY_TRIP_NOT_PUBLISHABLE",
        "COMMUNITY_POST_EXISTS",
        "COMMUNITY_PUBLISH_FAILED",
        "COMMUNITY_VALIDATION_FAILED",
    ]:
        payload = community_models.CommunityErrorResponse.model_validate(
            {"detail": {"code": code, "message": "stable"}}
        )
        assert payload.detail.code == code

    with pytest.raises(ValidationError):
        community_models.CommunityErrorResponse.model_validate(
            {"detail": {"code": "COMMUNITY_UNKNOWN", "message": "nope"}}
        )

    with pytest.raises(ValidationError):
        community_models.CommunityErrorResponse.model_validate(
            {
                "detail": {
                    "code": "COMMUNITY_POST_EXISTS",
                    "message": "stable",
                    "status": 409,
                }
            }
        )

    with pytest.raises(ValidationError):
        community_models.CommunityErrorResponse.model_validate(
            {
                "detail": {
                    "code": "COMMUNITY_POST_EXISTS",
                    "message": "stable",
                },
                "request_id": "extra",
            }
        )
