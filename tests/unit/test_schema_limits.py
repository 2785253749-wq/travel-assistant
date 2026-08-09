from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.schemas import ChatResponse, TravelProfile


@pytest.mark.parametrize(
    "payload",
    [
        {"origin": "x" * 201},
        {"destination": "x" * 201},
        {"preferences": ["p"] * 21},
        {"constraints": ["x" * 501]},
        {"travelers": 101},
        {"budget_cny": 10_000_001},
    ],
)
def test_travel_profile_rejects_oversized_fields_and_lists(payload):
    with pytest.raises(ValidationError):
        TravelProfile.model_validate(payload)


@pytest.mark.parametrize(
    "response_field",
    [
        {"warnings": ["warning"] * 41},
        {"warnings": ["x" * 501]},
        {
            "sources": [
                {
                    "evidence_id": "official-1",
                    "source_url": "https://example.com/source",
                    "source_type": "official",
                    "fetched_at": datetime(2026, 8, 7, tzinfo=UTC),
                    "freshness": "reference only",
                }
            ]
            * 101
        },
    ],
)
def test_chat_response_rejects_oversized_lists_and_items(response_field):
    with pytest.raises(ValidationError):
        ChatResponse(
            reply="done",
            stage="planned",
            profile=TravelProfile(),
            **response_field,
        )
