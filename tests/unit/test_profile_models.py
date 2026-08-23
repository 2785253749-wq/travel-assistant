from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.profile.models import ProfileInput, UserProfile


def test_profile_input_trims_string_fields():
    profile = ProfileInput(
        display_name="  Voyage Alice  ",
        bio="  Loves slow mornings and street food.  ",
        home_city="  Xiamen  ",
        travel_styles=["美食", "自然"],
    )

    assert profile.display_name == "Voyage Alice"
    assert profile.bio == "Loves slow mornings and street food."
    assert profile.home_city == "Xiamen"


def test_profile_input_requires_display_name_field_for_replace_semantics():
    with pytest.raises(ValidationError):
        ProfileInput.model_validate(
            {
                "bio": "",
                "home_city": "",
                "travel_styles": [],
            }
        )


def test_profile_input_accepts_avatar_path_and_user_profile_exposes_avatar_url_only():
    profile = ProfileInput.model_validate(
        {
            "display_name": "Alice",
            "bio": "",
            "home_city": "",
            "travel_styles": [],
            "avatar_path": "11111111-1111-1111-1111-111111111111/avatar/avatar.webp",
        }
    )

    assert profile.avatar_path == "11111111-1111-1111-1111-111111111111/avatar/avatar.webp"

    response = UserProfile.model_validate(
        {
            "user_id": str(uuid4()),
            "email": "alice@example.com",
            "display_name": "Alice",
            "bio": "",
            "home_city": "",
            "travel_styles": [],
            "avatar_url": "https://cdn.example.test/avatar.webp",
            "updated_at": datetime(2026, 8, 20, tzinfo=UTC),
        }
    )

    assert response.avatar_url == "https://cdn.example.test/avatar.webp"

    with pytest.raises(ValidationError):
        UserProfile.model_validate(
            {
                "user_id": str(uuid4()),
                "email": "alice@example.com",
                "display_name": "Alice",
                "bio": "",
                "home_city": "",
                "travel_styles": [],
                "avatar_path": "11111111-1111-1111-1111-111111111111/avatar/avatar.webp",
                "updated_at": datetime(2026, 8, 20, tzinfo=UTC),
            }
        )


@pytest.mark.parametrize(
    "payload",
    [
        {"display_name": "x" * 41, "bio": "", "home_city": "", "travel_styles": []},
        {"display_name": "", "bio": "x" * 161, "home_city": "", "travel_styles": []},
        {"display_name": "", "bio": "", "home_city": "x" * 41, "travel_styles": []},
    ],
)
def test_profile_input_rejects_oversized_fields(payload):
    with pytest.raises(ValidationError):
        ProfileInput.model_validate(payload)


def test_profile_input_accepts_only_allowlisted_travel_styles():
    with pytest.raises(ValidationError):
        ProfileInput.model_validate(
            {
                "display_name": "",
                "bio": "",
                "home_city": "",
                "travel_styles": ["美食", "海岛"],
            }
        )

    with pytest.raises(ValidationError):
        ProfileInput.model_validate(
            {
                "display_name": "",
                "bio": "",
                "home_city": "",
                "travel_styles": ["美食", "人文", "自然", "亲子", "户外", "休闲"],
            }
        )

    profile = ProfileInput.model_validate(
        {
            "display_name": "",
            "bio": "",
            "home_city": "",
            "travel_styles": ["美食", "人文", "自然", "亲子", "户外"],
        }
    )

    assert profile.travel_styles == ["美食", "人文", "自然", "亲子", "户外"]


def test_profile_models_reject_unknown_fields():
    with pytest.raises(ValidationError):
        ProfileInput.model_validate(
            {
                "display_name": "",
                "bio": "",
                "home_city": "",
                "travel_styles": [],
                "email": "private@example.com",
            }
        )

    with pytest.raises(ValidationError):
        UserProfile.model_validate(
            {
                "user_id": str(uuid4()),
                "email": "alice@example.com",
                "display_name": "Alice",
                "bio": "",
                "home_city": "",
                "travel_styles": [],
                "updated_at": datetime(2026, 8, 20, tzinfo=UTC),
                "preferences": {},
            }
        )
