from unittest.mock import Mock
from datetime import datetime, timezone

import pytest

from app.agent.graph import (
    ChatSessionStore,
    PlanClaim,
    PlanningResult,
    SafeTravelAgent,
    TrustedEvidence,
    chat,
)
from app.agent.intent import IntentResult
from app.agent.safety import assess_message
from app.schemas import TravelProfile
from app.agent.planning import PlanValidationError, Planner as StructuredPlanner


class StubClassifier:
    def __init__(self, intent: str = "plan_trip") -> None:
        self.intent = intent

    def classify(self, message: str, has_trip: bool) -> IntentResult:
        return IntentResult(intent=self.intent, confidence=0.9)


class StubExtractor:
    def extract(self, message: str, profile: TravelProfile) -> TravelProfile:
        return profile


class StubEvidenceProvider:
    def __init__(self, evidence: list[TrustedEvidence] | None = None) -> None:
        self.evidence = evidence or []

    def fetch(self, profile: TravelProfile) -> list[TrustedEvidence]:
        return self.evidence


def make_agent(
    *, intent: str = "plan_trip", profile: TravelProfile | None = None,
    planner=None, evidence_provider=None,
):
    return SafeTravelAgent(
        classifier=StubClassifier(intent),
        extractor=StubExtractor(),
        planner=planner or Mock(),
        evidence_provider=evidence_provider or StubEvidenceProvider(),
        initial_profile=profile or TravelProfile(origin="上海"),
    )


def test_missing_fields_asks_without_calling_planner():
    planner = Mock()
    result = make_agent(planner=planner).run("从上海出发", trip=None)

    assert result.stage == "collecting"
    assert "目的地" in result.reply
    planner.invoke.assert_not_called()


def test_live_inventory_question_is_refused():
    result = make_agent().run("保证明天还有两张高铁票并帮我买", trip=None)

    assert result.error_code == "UNVERIFIABLE_REALTIME_REQUEST"
    assert "12306" in result.reply


def test_modify_without_trip_routes_to_creation():
    result = make_agent(intent="modify_trip").run("把第二天改成西湖", trip=None)

    assert result.stage == "collecting"
    assert "先告诉我" in result.reply


def test_invalid_profile_is_collected_without_calling_planner():
    planner = Mock()
    result = make_agent(
        profile=TravelProfile(
            origin="上海", destination="杭州", start_date="2026-10-01",
            end_date="2026-10-08", travelers=2, budget_cny=3000,
        ),
        planner=planner,
    ).run("规划行程", trip=None)

    assert result.stage == "collecting"
    assert result.issues[0].code == "trip_duration"
    planner.invoke.assert_not_called()


def test_invalid_extracted_traveler_count_asks_without_storing_an_invalid_profile():
    from app.agent.extraction import ExtractionCandidate
    from app.schemas import ProfileIssue

    class InvalidTravelerExtractor:
        def extract(self, message: str, profile: TravelProfile) -> ExtractionCandidate:
            return ExtractionCandidate(
                TravelProfile(
                    origin="上海", destination="苏州", start_date="2026-10-01",
                    end_date="2026-10-03", budget_cny=2000,
                ),
                issues=(ProfileIssue(
                    code="traveler_count", field="travelers", message="仅支持 1 至 6 人出行。",
                ),),
                invalid_fields={"travelers": 0},
            )

    planner = Mock()
    result = SafeTravelAgent(
        classifier=StubClassifier(), extractor=InvalidTravelerExtractor(), planner=planner,
    ).run("上海去苏州，0个人，预算2000", trip=None)

    assert result.stage == "collecting"
    assert [(issue.code, issue.field) for issue in result.issues] == [
        ("traveler_count", "travelers"),
    ]
    assert result.profile["travelers"] is None
    planner.invoke.assert_not_called()


def test_unverified_planner_response_is_marked_before_returning():
    planner = Mock()
    planner.invoke.return_value = "建议住在市中心，票价是 100 元。"
    result = make_agent(
        profile=TravelProfile(
            origin="上海", destination="杭州", start_date="2026-10-01",
            end_date="2026-10-02", travelers=2, budget_cny=3000,
        ),
        planner=planner,
    ).run("规划行程", trip=None)

    assert result.stage == "planned"
    assert "待确认" in result.reply


def test_agent_error_does_not_expose_internal_details():
    planner = Mock()
    planner.invoke.side_effect = RuntimeError("postgres password=secret")
    result = make_agent(
        profile=TravelProfile(
            origin="上海", destination="杭州", start_date="2026-10-01",
            end_date="2026-10-02", travelers=2, budget_cny=3000,
        ),
        planner=planner,
        evidence_provider=StubEvidenceProvider([
            TrustedEvidence("ev-1", "建议按区域游览", "https://www.gov.cn/", "official")
        ]),
    ).run("规划行程", trip=None)

    assert result.error_code == "AGENT_UNAVAILABLE"
    assert "secret" not in result.reply


def test_explicit_foreign_destination_is_refused_after_plan_intent():
    planner = Mock()
    result = make_agent(
        profile=TravelProfile(
            origin="北京", destination="东京", start_date="2026-10-01",
            end_date="2026-10-03", travelers=2, budget_cny=3000,
        ),
        planner=planner,
    ).run("北京出发去东京玩3天", trip=None)

    assert result.error_code == "OUT_OF_SCOPE"
    planner.invoke.assert_not_called()


def test_germany_and_berlin_are_stably_out_of_scope():
    result = make_agent(
        profile=TravelProfile(
            origin="北京", destination="德国柏林", start_date="2026-10-01",
            end_date="2026-10-03", travelers=2, budget_cny=3000,
        ),
    ).run("北京出发去德国柏林", trip=None)

    assert result.error_code == "OUT_OF_SCOPE"


def test_explicit_domestic_destination_reaches_planner():
    planner = Mock()
    planner.invoke.return_value = PlanningResult([
        PlanClaim("浙江省博物馆需要实名预约", "ev-zhejiang")
    ])
    evidence_provider = StubEvidenceProvider([
        TrustedEvidence(
            "ev-zhejiang", "浙江省博物馆需要实名预约",
            "https://www.zhejiang.gov.cn/", "official",
        )
    ])
    result = make_agent(
        profile=TravelProfile(
            origin="北京", destination="杭州", start_date="2026-10-01",
            end_date="2026-10-03", travelers=2, budget_cny=3000,
        ),
        planner=planner, evidence_provider=evidence_provider,
    ).run("北京出发去杭州玩3天", trip=None)

    assert result.stage == "planned"
    planner.invoke.assert_called_once()
    assert "浙江省博物馆" in result.reply


def test_mainland_province_and_city_are_allowlisted():
    from app.agent.safety import assess_destination

    assert assess_destination("浙江省杭州市").allowed
    assert assess_destination("四川省成都市").allowed


@pytest.mark.parametrize("destination", ["兰州", "兰州市", "西宁", "西宁市"])
def test_new_domestic_city_aliases_are_allowlisted(destination: str):
    from app.agent.safety import assess_destination

    assert assess_destination(destination).allowed


@pytest.mark.parametrize(
    ("message", "expected_code"),
    [
        ("查明天上海到北京的实时机票价格", "UNVERIFIABLE_REALTIME_REQUEST"),
        ("保证夜游西安绝对安全", "HIGH_STAKES_ADVICE"),
        ("给我保证不会发生地震的旅行建议", "HIGH_STAKES_ADVICE"),
    ],
)
def test_unverifiable_realtime_and_guaranteed_safety_requests_are_refused(
    message: str, expected_code: str,
):
    result = make_agent().run(message, trip=None)

    assert result.error_code == expected_code


def test_ordinary_travel_safety_advice_is_not_refused():
    result = make_agent().run("给我夜游西安的安全建议", trip=None)

    assert result.error_code != "HIGH_STAKES_ADVICE"


@pytest.mark.parametrize(
    "message",
    [
        "明天住什么酒店比较方便",
        "如何确保夜游安全",
    ],
)
def test_ordinary_timed_lodging_and_safety_guidance_are_not_refused(message: str):
    result = make_agent().run(message, trip=None)

    assert result.error_code not in {
        "UNVERIFIABLE_REALTIME_REQUEST",
        "HIGH_STAKES_ADVICE",
    }


@pytest.mark.parametrize("message", ["确保我人身安全", "确保旅途安全"])
def test_direct_safety_guarantees_are_refused(message: str):
    result = make_agent().run(message, trip=None)

    assert result.error_code == "HIGH_STAKES_ADVICE"


@pytest.mark.parametrize(
    "message",
    ["如何确保夜游安全", "确保带上安全装备", "明天从上海飞北京怎么安排"],
)
def test_safety_precautions_and_ordinary_flight_planning_are_not_refused(message: str):
    result = make_agent().run(message, trip=None)

    assert result.error_code not in {
        "UNVERIFIABLE_REALTIME_REQUEST",
        "HIGH_STAKES_ADVICE",
    }


@pytest.mark.parametrize(
    "message",
    ["确保大家的安全措施到位", "机票价格不用查，明天只帮我安排行程"],
)
def test_practical_safety_measures_and_explicit_price_lookup_opt_out_are_not_refused(
    message: str,
):
    result = make_agent().run(message, trip=None)

    assert result.error_code not in {
        "UNVERIFIABLE_REALTIME_REQUEST",
        "HIGH_STAKES_ADVICE",
    }


@pytest.mark.parametrize(
    ("message", "expected_code"),
    [
        ("确保大家的安全措施到位，也确保旅途安全", "HIGH_STAKES_ADVICE"),
        ("机票价格不用查，明天酒店价格多少", "UNVERIFIABLE_REALTIME_REQUEST"),
    ],
)
def test_exemption_in_one_clause_does_not_suppress_a_separate_refusal(
    message: str, expected_code: str,
):
    result = make_agent().run(message, trip=None)

    assert result.error_code == expected_code


@pytest.mark.parametrize("separator", ["：", ":", "\n", "\r\n"])
def test_lookup_opt_out_does_not_cross_colon_or_newline_clause_boundaries(
    separator: str,
):
    result = make_agent().run(
        f"机票价格不用查{separator}明天酒店价格多少",
        trip=None,
    )

    assert result.error_code == "UNVERIFIABLE_REALTIME_REQUEST"


@pytest.mark.parametrize("message", ["明天机票：价格多少", "明天机票\n价格多少"])
def test_realtime_signals_across_adjacent_clauses_are_refused(message: str):
    assert assess_message(message).code == "UNVERIFIABLE_REALTIME_REQUEST"


def test_opt_out_only_applies_to_its_own_clause():
    assert assess_message("机票价格不用查：明天只帮我安排行程").code is None
    assert (
        assess_message("机票价格不用查：明天酒店价格多少").code
        == "UNVERIFIABLE_REALTIME_REQUEST"
    )


@pytest.mark.parametrize(
    "message",
    ["明天机票：价格不用查，只帮我安排行程", "明天机票\n价格不用查，只帮我安排行程"],
)
def test_trailing_opt_out_exempts_its_adjacent_realtime_window(message: str):
    assert assess_message(message).code is None


@pytest.mark.parametrize(
    "message",
    ["明天酒店价格多少：机票价格不用查", "明天酒店价格多少\n机票价格不用查"],
)
def test_unrelated_trailing_opt_out_does_not_suppress_realtime_request(message: str):
    assert assess_message(message).code == "UNVERIFIABLE_REALTIME_REQUEST"


@pytest.mark.parametrize(
    "message",
    [
        "明天酒店和机票价格多少：机票价格不用查",
        "明天酒店和机票价格多少\n机票价格不用查",
        "明天车票价格多少：机票价格不用查",
        "明天车票价格多少\n机票价格不用查",
    ],
)
def test_opt_out_must_cover_every_requested_dynamic_category(message: str):
    assert assess_message(message).code == "UNVERIFIABLE_REALTIME_REQUEST"


@pytest.mark.parametrize(
    "message",
    ["明天航班价格多少：机票价格不用查", "明天航班价格多少\n机票价格不用查"],
)
def test_flight_synonym_opt_out_exempts_the_same_dynamic_request(message: str):
    assert assess_message(message).code is None


def test_generic_price_opt_out_exempts_categoryless_price_request():
    assert assess_message("明天票价是多少：票价不用查").code is None


def test_opt_out_categories_only_include_subjects_governed_by_negation():
    message = "明天酒店和机票价格多少：机票价格不用查酒店价格照样查"

    assert assess_message(message).code == "UNVERIFIABLE_REALTIME_REQUEST"


@pytest.mark.parametrize(
    "opt_out",
    ["机票不用查价格", "机票无需查价格", "机票不必查价格", "机票别查价格"],
)
def test_subject_before_negation_scopes_reversed_opt_out(opt_out: str):
    message = f"明天酒店价格多少：{opt_out}"

    assert assess_message(message).code == "UNVERIFIABLE_REALTIME_REQUEST"


@pytest.mark.parametrize(
    "opt_out",
    ["机票价格和酒店价格都不用查", "不用查机票价格也不用查酒店价格"],
)
def test_multi_target_opt_out_covers_every_requested_category(opt_out: str):
    message = f"明天酒店和机票价格多少：{opt_out}"

    assert assess_message(message).code is None


def test_positive_lookup_in_opt_out_clause_remains_a_realtime_request():
    message = "明天机票价格多少：机票价格不用查酒店价格多少"

    assert assess_message(message).code == "UNVERIFIABLE_REALTIME_REQUEST"


@pytest.mark.parametrize("separator", ["，", "；", ";"])
def test_practical_measure_does_not_exempt_guarantee_in_earlier_clause(
    separator: str,
):
    result = make_agent().run(
        f"确保旅途安全{separator}也落实安全措施",
        trip=None,
    )

    assert result.error_code == "HIGH_STAKES_ADVICE"


def test_concise_timed_ticket_price_request_is_refused():
    result = make_agent().run("明天票价是多少", trip=None)

    assert result.error_code == "UNVERIFIABLE_REALTIME_REQUEST"


def test_unknown_destination_is_not_guessed_or_planned():
    planner = Mock()
    result = make_agent(
        profile=TravelProfile(
            origin="北京", destination="神秘岛", start_date="2026-10-01",
            end_date="2026-10-03", travelers=2, budget_cny=3000,
        ),
        planner=planner,
    ).run("规划行程", trip=None)

    assert result.error_code == "DESTINATION_UNDETERMINED"
    planner.invoke.assert_not_called()


def test_unverified_variable_planner_claims_are_not_passed_through():
    planner = Mock()
    planner.invoke.return_value = "酒店可订，门票价格 100 元，景点营业到 21:00。"
    result = make_agent(
        profile=TravelProfile(
            origin="北京", destination="杭州", start_date="2026-10-01",
            end_date="2026-10-03", travelers=2, budget_cny=3000,
        ),
        planner=planner,
    ).run("规划行程", trip=None)

    assert "酒店可订" not in result.reply
    assert "100 元" not in result.reply
    assert "21:00" not in result.reply
    assert "待确认" in result.reply


def test_planner_forged_evidence_id_and_self_reported_url_are_rejected():
    planner = Mock()
    planner.invoke.return_value = PlanningResult([
        PlanClaim("酒店今晚可订，价格 399 元", "forged-evidence-id")
    ])
    result = make_agent(
        profile=TravelProfile(
            origin="北京", destination="杭州", start_date="2026-10-01",
            end_date="2026-10-03", travelers=2, budget_cny=3000,
        ),
        planner=planner,
        evidence_provider=StubEvidenceProvider([
            TrustedEvidence(
                "real-evidence-id", "西湖位于杭州市",
                "https://www.hangzhou.gov.cn/", "official",
            )
        ]),
    ).run("规划行程", trip=None)

    assert "酒店今晚可订" not in result.reply
    assert "399" not in result.reply
    assert result.error_code == "UNVERIFIED_FACTS"


def test_claim_text_must_exactly_match_trusted_evidence_registry():
    planner = Mock()
    planner.invoke.return_value = PlanningResult([
        PlanClaim("西湖门票价格已确认是 100 元", "ev-hangzhou")
    ])
    result = make_agent(
        profile=TravelProfile(
            origin="北京", destination="杭州", start_date="2026-10-01",
            end_date="2026-10-03", travelers=2, budget_cny=3000,
        ), planner=planner,
        evidence_provider=StubEvidenceProvider([
            TrustedEvidence("ev-hangzhou", "西湖位于杭州市", "https://www.hangzhou.gov.cn/", "official")
        ]),
    ).run("规划行程", trip=None)

    assert "100 元" not in result.reply
    assert result.error_code == "UNVERIFIED_FACTS"


def test_thread_profiles_are_isolated_and_resume_without_raw_messages(monkeypatch):
    store = ChatSessionStore(max_sessions=2)
    profiles = iter([
        TravelProfile(destination="杭州", start_date="2026-10-01", end_date="2026-10-03"),
        TravelProfile(travelers=2, budget_cny=3000, origin="北京"),
    ])

    class Extractor:
        def extract(self, message, profile):
            return next(profiles)

    class Planner:
        def invoke(self, profile, evidence):
            return PlanningResult([PlanClaim("杭州位于浙江省", "ev-hangzhou")])

    monkeypatch.setattr("app.agent.graph._chat_store", store)
    monkeypatch.setattr("app.agent.graph.SafeTravelAgent", lambda **kwargs: SafeTravelAgent(
        classifier=StubClassifier(), extractor=Extractor(), planner=Planner(),
        evidence_provider=StubEvidenceProvider([
            TrustedEvidence("ev-hangzhou", "杭州位于浙江省", "https://www.hangzhou.gov.cn/", "official")
        ]), **kwargs
    ))

    first = chat(None, None, "杭州 10 月 1 日到 3 日", thread_id="thread-a", session_scope="anon:a")
    second = chat(None, None, "2 人预算 3000 从北京出发", thread_id="thread-a", session_scope="anon:a")

    assert first.stage == "collecting"
    assert second.stage == "planned"
    assert store.get("anon:a", "thread-a").travelers == 2
    assert store.get("anon:b", "thread-a") is None


def test_profile_with_jwt_or_secret_pattern_is_not_stored():
    store = ChatSessionStore(max_sessions=2)

    assert not store.put(
        "anon:a", "thread-a",
        TravelProfile(destination="杭州", preferences=["Bearer eyJhbGciOiJIUzI1NiJ9.secret.signature"]),
    )
    assert store.get("anon:a", "thread-a") is None


def test_agent_logs_only_stable_error_metadata(caplog):
    planner = Mock()
    planner.invoke.side_effect = RuntimeError("Bearer jwt-super-secret provider password")
    with caplog.at_level("WARNING", logger="app.agent"):
        make_agent(
            profile=TravelProfile(
                origin="北京", destination="杭州", start_date="2026-10-01",
                end_date="2026-10-03", travelers=2, budget_cny=3000,
            ), planner=planner,
            evidence_provider=StubEvidenceProvider([
                TrustedEvidence("ev-1", "杭州位于浙江省", "https://www.hangzhou.gov.cn/", "official")
            ]),
        ).run("规划行程", trip=None)

    record = next(record for record in caplog.records if record.message == "agent_failed")
    assert record.error_code == "AGENT_UNAVAILABLE"
    assert record.exception_type == "RuntimeError"
    assert "jwt-super-secret" not in caplog.text


def test_agent_uses_structured_planner_repair_and_fails_closed_after_second_error():
    profile = TravelProfile(origin="北京", destination="杭州", start_date="2026-10-01", end_date="2026-10-02", travelers=2, budget_cny=3000)
    now = datetime(2026, 7, 2, tzinfo=timezone.utc)
    valid = {
        "title": "Weekend", "start_date": "2026-10-01", "end_date": "2026-10-02",
        "days": [
            {"date": "2026-10-01", "morning": {"title": "Walk", "start_time": "09:00", "end_time": "11:00"}, "afternoon": {"title": "Museum", "start_time": "13:00", "end_time": "15:00"}, "evening": {"title": "Dinner", "start_time": "18:00", "end_time": "20:00"}},
            {"date": "2026-10-02", "morning": {"title": "Park", "start_time": "09:00", "end_time": "11:00"}, "afternoon": {"title": "Market", "start_time": "13:00", "end_time": "15:00"}, "evening": {"title": "Return", "start_time": "17:00", "end_time": "19:00"}},
        ],
        "budget": {"transport": 800, "hotel": 1000, "food": 800, "tickets": 200, "reserve": 200, "other": 0, "total": 3000, "trip_total": 3000, "currency": "CNY", "traveler_basis": "trip_total", "traveler_count": 2, "estimate": {"low": 2800, "point": 3000, "high": 3200, "currency": "CNY", "basis": "trip_total", "assumption_id": "cost-v1"}},
        "notes": [], "assumptions": [{"assumption_id": "cost-v1", "category": "budget", "description": "Offline planning estimate."}],
    }
    calls = []
    def repaired(_profile, _sources, repair_codes):
        calls.append(repair_codes)
        return {"invalid": True} if repair_codes is None else valid
    evidence = StubEvidenceProvider([TrustedEvidence("ev-1", "West Lake is in Hangzhou.", "https://provider.example/place", "trusted_provider", now)])

    result = make_agent(profile=profile, planner=StructuredPlanner(repaired, now=lambda: now), evidence_provider=evidence).run("plan", trip=None)

    assert result.stage == "planned"
    assert calls == [None, ["SCHEMA_INVALID"]]

    failed = make_agent(profile=profile, planner=StructuredPlanner(lambda *_: {"invalid": True}, now=lambda: now), evidence_provider=evidence).run("plan", trip=None)
    assert failed.error_code == "PLAN_VALIDATION_FAILED"
