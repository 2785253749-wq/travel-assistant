from unittest.mock import Mock

from app.agent.graph import (
    ChatSessionStore,
    PlanClaim,
    PlanningResult,
    SafeTravelAgent,
    TrustedEvidence,
    chat,
)
from app.agent.intent import IntentResult
from app.schemas import TravelProfile


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
