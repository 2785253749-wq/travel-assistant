import pytest
from pydantic import ValidationError

from app.agent.extraction import merge_profile, validate_profile
from app.agent.graph import RuleTravelExtractor, extract_profile
from app.schemas import TravelProfile


@pytest.mark.parametrize(
    ("message", "origin", "destination"),
    [
        ("From Shanghai to Xiamen, 2026-10-01 to 2026-10-03", "Shanghai", "Xiamen"),
        ("\u4ece\u4e0a\u6d77\u5230\u53a6\u95e8", "\u4e0a\u6d77", "\u53a6\u95e8"),
    ],
)
def test_rule_extractor_reads_explicit_chinese_and_english_routes(message, origin, destination):
    profile = RuleTravelExtractor().extract(message, TravelProfile())

    assert profile.origin == origin
    assert profile.destination == destination


@pytest.mark.parametrize(
    ("message", "start_date", "end_date"),
    [
        ("福州到厦门，2026.8.16到2026.8.18，2人，预算5000", "2026-08-16", "2026-08-18"),
        ("从福州到厦门，2026年8月16日至2026年8月18日", "2026-08-16", "2026-08-18"),
    ],
)
def test_rule_extractor_normalizes_common_route_and_date_formats(message, start_date, end_date):
    profile = RuleTravelExtractor().extract(message, TravelProfile())

    assert profile.origin == "福州"
    assert profile.destination == "厦门"
    assert profile.start_date == start_date
    assert profile.end_date == end_date


@pytest.mark.parametrize("traveler_prefix", ["2人", "两人"])
def test_rule_extractor_does_not_include_traveler_prefix_in_origin(traveler_prefix):
    profile = RuleTravelExtractor().extract(
        f"{traveler_prefix}从福州到厦门，2026.8.16到2026.8.18，预算5000",
        TravelProfile(),
    )

    assert profile.origin == "福州"
    assert profile.destination == "厦门"
    assert profile.travelers == 2


class _StructuredExtractionModel:
    def __init__(self, payload: dict[str, object]) -> None:
        self._payload = payload

    def with_structured_output(self, schema: object, *, method: str) -> "_StructuredExtractionModel":
        assert method == "json_mode"
        return self

    def invoke(self, _: object) -> dict[str, object]:
        return {"profile": self._payload}


@pytest.mark.parametrize("travelers", [0, -1, 7])
def test_task2_extraction_keeps_invalid_traveler_out_of_travel_profile(travelers: int):
    extraction = extract_profile(
        "上海去苏州，人数需要确认，预算2000",
        TravelProfile(origin="上海"),
        model_factory=lambda: _StructuredExtractionModel({
            "destination": "苏州", "travelers": travelers, "budget_cny": 2000,
        }),
    )

    assert extraction.profile == TravelProfile(origin="上海", destination="苏州", budget_cny=2000)
    assert extraction.invalid_fields == {"travelers": travelers}
    assert [(issue.code, issue.field) for issue in extraction.issues] == [
        ("traveler_count", "travelers"),
    ]


@pytest.mark.parametrize("travelers", [1, 6])
def test_task2_extraction_accepts_supported_traveler_bounds(travelers: int):
    extraction = extract_profile(
        "上海去苏州",
        TravelProfile(origin="上海"),
        model_factory=lambda: _StructuredExtractionModel({
            "destination": "苏州", "travelers": travelers,
        }),
    )

    assert extraction.profile.travelers == travelers
    assert extraction.invalid_fields == {}
    assert extraction.issues == ()


def test_empty_extraction_does_not_erase_confirmed_values():
    current = TravelProfile(origin="上海", travelers=2)
    extracted = TravelProfile(origin=None, travelers=None)

    assert merge_profile(current, extracted) == current


def test_merge_profile_applies_explicit_extracted_values():
    current = TravelProfile(origin="上海", travelers=2, preferences=["美食"])
    extracted = TravelProfile(destination="杭州", travelers=3, preferences=["自然"])

    merged = merge_profile(current, extracted)

    assert merged == TravelProfile(
        origin="上海", destination="杭州", travelers=3, preferences=["自然"]
    )


def test_profile_validation_returns_stable_codes_for_product_boundaries():
    profile = TravelProfile(
        start_date="2026-10-05",
        end_date="2026-10-01",
        travelers=7,
    )

    issues = validate_profile(profile)

    assert [(issue.code, issue.field) for issue in issues] == [
        ("date_order", "end_date"),
        ("traveler_count", "travelers"),
    ]


def test_profile_validation_rejects_trips_longer_than_seven_days():
    profile = TravelProfile(start_date="2026-10-01", end_date="2026-10-08")

    issues = validate_profile(profile)

    assert [(issue.code, issue.field) for issue in issues] == [
        ("trip_duration", "end_date"),
    ]


@pytest.mark.parametrize("travelers", [0, -1])
def test_travel_profile_rejects_nonpositive_traveler_counts(travelers):
    with pytest.raises(ValidationError):
        TravelProfile(travelers=travelers)


@pytest.mark.parametrize("travelers", [0, -1])
def test_profile_validation_keeps_stable_code_for_bypassed_nonpositive_counts(travelers):
    profile = TravelProfile.model_construct(travelers=travelers)

    issues = validate_profile(profile)

    assert [(issue.code, issue.field) for issue in issues] == [
        ("traveler_count", "travelers"),
    ]


def test_profile_validation_rejects_compact_date_format():
    profile = TravelProfile(start_date="20261001")

    issues = validate_profile(profile)

    assert [(issue.code, issue.field) for issue in issues] == [
        ("invalid_date", "start_date"),
    ]
