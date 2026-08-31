from datetime import date, timedelta

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
    "message",
    [
        "从上海往南京",
        "从上海前往南京",
        "上海去往南京",
        "由上海抵达南京",
        "从上海到南京玩三天",
    ],
)
def test_rule_extractor_reads_common_chinese_route_synonyms(message):
    profile = RuleTravelExtractor().extract(message, TravelProfile())

    assert profile.origin == "上海"
    assert profile.destination == "南京"


@pytest.mark.parametrize(
    ("message", "origin", "destination"),
    [
        ("从福州去上海", "福州", "上海"),
        ("从福州到上海", "福州", "上海"),
        ("福州去上海", "福州", "上海"),
        ("福州到上海", "福州", "上海"),
        ("明天两个人从福州去上海玩两天，预算6000元", "福州", "上海"),
    ],
)
def test_rule_extractor_splits_chinese_routes_at_city_boundaries(message, origin, destination):
    profile = RuleTravelExtractor().extract(message, TravelProfile())

    assert profile.origin == origin
    assert profile.destination == destination


@pytest.mark.parametrize(
    ("message", "start_date", "end_date", "travelers", "budget"),
    [
        (
            "帮我规划明天两个人从福州去上海玩两天，预算6000元",
            "2026-08-31",
            "2026-09-01",
            2,
            6000,
        ),
        (
            "后天三个人福州去上海玩3天，预算8000",
            "2026-09-01",
            "2026-09-03",
            3,
            8000,
        ),
        (
            "明天福州去上海两日游，两人，预算6000",
            "2026-08-31",
            "2026-09-01",
            2,
            6000,
        ),
    ],
)
def test_rule_extractor_extracts_relative_dates_duration_and_travelers(
    message, start_date, end_date, travelers, budget
):
    profile = RuleTravelExtractor(reference_date=date(2026, 8, 30)).extract(
        message,
        TravelProfile(),
    )

    assert profile.origin == "福州"
    assert profile.destination == "上海"
    assert profile.start_date == start_date
    assert profile.end_date == end_date
    assert profile.travelers == travelers
    assert profile.budget_cny == budget


@pytest.mark.parametrize(
    ("relative_day", "expected"),
    [
        ("今天", "2026-08-30"),
        ("明天", "2026-08-31"),
        ("后天", "2026-09-01"),
    ],
)
def test_rule_extractor_resolves_relative_start_dates(relative_day, expected):
    profile = RuleTravelExtractor(reference_date=date(2026, 8, 30)).extract(
        f"{relative_day}福州去上海玩两天",
        TravelProfile(),
    )

    assert profile.start_date == expected
    assert profile.end_date == (date.fromisoformat(expected) + timedelta(days=1)).isoformat()


def test_rule_extractor_prefers_explicit_end_date_over_duration():
    profile = RuleTravelExtractor(reference_date=date(2026, 8, 30)).extract(
        "福州去上海，2026-09-10到2026-09-12，玩两天",
        TravelProfile(),
    )

    assert profile.start_date == "2026-09-10"
    assert profile.end_date == "2026-09-12"


@pytest.mark.parametrize(
    ("traveler_text", "expected"),
    [("1个人", 1), ("2个人", 2), ("3个人", 3), ("两人", 2), ("三人", 3)],
)
def test_rule_extractor_reads_supported_traveler_phrases(traveler_text, expected):
    profile = RuleTravelExtractor().extract(
        f"{traveler_text}从福州去上海",
        TravelProfile(),
    )

    assert profile.travelers == expected


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("2026-09-01两个人从福州去上海玩两天", 2),
        ("2026-09-01 两个人从福州去上海玩两天", 2),
        ("2026-09-01，2个人从福州去上海玩两天", 2),
        ("2026-09-01三人从福州去上海玩3天", 3),
    ],
)
def test_rule_extractor_reads_travelers_after_explicit_date(message, expected):
    profile = RuleTravelExtractor(reference_date=date(2026, 8, 31)).extract(
        message,
        TravelProfile(),
    )

    assert profile.travelers == expected


def test_rule_extractor_reads_complete_trip_when_date_and_travelers_are_adjacent():
    profile = RuleTravelExtractor(reference_date=date(2026, 8, 31)).extract(
        "帮我规划2026-09-01两个人从福州去上海玩两天，预算6000元",
        TravelProfile(),
    )

    assert profile.origin == "福州"
    assert profile.destination == "上海"
    assert profile.start_date == "2026-09-01"
    assert profile.end_date == "2026-09-02"
    assert profile.travelers == 2
    assert profile.budget_cny == 6000


@pytest.mark.parametrize(
    ("duration_text", "expected_end_date"),
    [("去两天", "2026-09-01"), ("待3天", "2026-09-02"), ("3日游", "2026-09-02")],
)
def test_rule_extractor_derives_end_date_from_supported_duration_phrases(
    duration_text, expected_end_date
):
    profile = RuleTravelExtractor(reference_date=date(2026, 8, 30)).extract(
        f"明天福州去上海{duration_text}",
        TravelProfile(),
    )

    assert profile.start_date == "2026-08-31"
    assert profile.end_date == expected_end_date


def test_rule_extractor_does_not_treat_compact_date_range_as_route():
    profile = RuleTravelExtractor(reference_date=date(2026, 8, 24)).extract(
        "2人8.25到8.27三天上海到南京预算9000",
        TravelProfile(),
    )

    assert profile.origin == "上海"
    assert profile.destination == "南京"
    assert profile.start_date == "2026-08-25"
    assert profile.end_date == "2026-08-27"
    assert profile.travelers == 2
    assert profile.budget_cny == 9000


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


@pytest.mark.parametrize(
    "message",
    [
        "8.26出发，8.27返回",
        "八月二十六出发，八月二十七返回",
    ],
)
def test_rule_extractor_normalizes_yearless_chinese_dates(message):
    profile = RuleTravelExtractor(reference_date=date(2026, 8, 24)).extract(
        message,
        TravelProfile(),
    )

    assert profile.start_date == "2026-08-26"
    assert profile.end_date == "2026-08-27"


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
