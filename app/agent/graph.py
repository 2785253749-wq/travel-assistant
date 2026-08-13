"""Safe, testable travel-agent orchestration.

Control-flow decisions remain deterministic; models only classify/extract/generate.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, TypedDict
from datetime import UTC, datetime
from uuid import UUID

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_deepseek import ChatDeepSeek

from app.agent.extraction import ExtractionCandidate, build_extraction_candidate, merge_profile, validate_profile
from app.agent.intent import Intent, IntentResult, classify_intent
from app.agent.safety import REFUSALS, assess_destination, assess_message
from app.core.config import get_settings
from app.core.logging import operational_context
from app.core.usage import ProviderUnavailable, get_model_gateway
from app.schemas import (
    CHAT_REPLY_MAX_LENGTH,
    ExtractionResult,
    Itinerary,
    ItineraryWeather,
    ProfileIssue,
    TravelProfile,
    WeatherCard,
)
from app.rag.service import RagAnswer, UnavailableKnowledgeAnswerService
from app.trips.models import Trip


REQUIRED_FIELDS = ("origin", "destination", "start_date", "end_date", "travelers", "budget_cny")
WORKFLOW_NODES = (
    "load_context", "classify", "guard_scope", "extract", "validate",
    "ask_or_confirm", "enrich", "plan", "verify", "persist",
)
FIELD_LABELS = {
    "origin": "出发地", "destination": "目的地", "start_date": "出发日期",
    "end_date": "返回日期", "travelers": "出行人数", "budget_cny": "总预算",
}


class TravelState(TypedDict, total=False):
    user_message: str
    intent: Intent
    profile: dict[str, Any]
    issues: list[ProfileIssue]
    sources: list[dict[str, Any]]
    reply: str
    stage: Literal["collecting", "confirming", "planned"]
    error_code: str | None


@dataclass
class ChatResult:
    reply: str
    stage: Literal["collecting", "confirming", "planned"]
    profile: dict[str, Any]
    issues: list[ProfileIssue] = field(default_factory=list)
    sources: list[dict[str, Any]] = field(default_factory=list)
    error_code: str | None = None
    itinerary: Itinerary | None = None
    warnings: list[str] = field(default_factory=list)
    trip_id: UUID | None = None
    intent: Intent | None = None
    persisted_this_request: bool = False


@dataclass(frozen=True)
class PlanClaim:
    text: str
    evidence_id: str | None = None


@dataclass(frozen=True)
class PlanningResult:
    claims: list[PlanClaim]


@dataclass(frozen=True)
class TrustedEvidence:
    evidence_id: str
    fact: str
    source_url: str
    source_type: str
    fetched_at: datetime | None = None


class ChatSessionStore:
    """Bounded in-process state containing only structured travel profiles."""

    def __init__(self, max_sessions: int = 200) -> None:
        self._max_sessions = max_sessions
        self._profiles: OrderedDict[tuple[str, str], TravelProfile] = OrderedDict()

    def get(self, subject_scope: str, thread_id: str) -> TravelProfile | None:
        key = self._key(subject_scope, thread_id)
        profile = self._profiles.get(key)
        if profile is not None:
            self._profiles.move_to_end(key)
        return profile

    def put(self, subject_scope: str, thread_id: str, profile: TravelProfile) -> bool:
        if _profile_contains_secret(profile):
            return False
        key = self._key(subject_scope, thread_id)
        self._profiles[key] = TravelProfile.model_validate(profile.model_dump())
        self._profiles.move_to_end(key)
        while len(self._profiles) > self._max_sessions:
            self._profiles.popitem(last=False)
        return True

    def clear(self, subject_scope: str, thread_id: str) -> None:
        self._profiles.pop(self._key(subject_scope, thread_id), None)

    @staticmethod
    def _key(subject_scope: str, thread_id: str) -> tuple[str, str]:
        return subject_scope, hashlib.sha256(thread_id.encode("utf-8")).hexdigest()


class IntentClassifier(Protocol):
    def classify(self, message: str, has_trip: bool) -> IntentResult: ...


class TravelExtractor(Protocol):
    def extract(self, message: str, profile: TravelProfile) -> ExtractionCandidate | TravelProfile: ...


class LegacyPlanner(Protocol):
    def invoke(
        self, profile: TravelProfile, evidence: tuple[TrustedEvidence, ...]
    ) -> PlanningResult: ...


class TrustedEvidenceProvider(Protocol):
    def fetch(self, profile: TravelProfile) -> list[TrustedEvidence]: ...


class KnowledgeAnswerer(Protocol):
    def answer(self, question: str, region: str | None = None) -> RagAnswer: ...


class DailyWeatherProvider(Protocol):
    def city_card(self, city_id: str): ...
    def daily_weather(self, destination: str, travel_date): ...


class NullWeatherService:
    """Keep weather optional without importing provider wiring into the agent."""

    def city_card(self, city_id: str) -> WeatherCard:
        return WeatherCard(city=city_id, status="unavailable", summary="天气信息暂不可用")

    def daily_weather(self, destination: str, travel_date) -> None:
        del destination, travel_date
        return None


class UsageGuard(Protocol):
    def allow(self, user_id: UUID | None) -> bool: ...


class ModelIntentClassifier:
    def classify(self, message: str, has_trip: bool) -> IntentResult:
        return classify_intent(message, has_trip)


class RuleIntentClassifier:
    """Credential-free pre-confirmation routing; paid models are planning-only."""

    _COMPLETE_PLAN_ROUTE = re.compile(r"(?:从|from)\s*[^\s，,。]{1,30}?\s*(?:到|去|to)\s*[^\s，,。]{1,30}", re.IGNORECASE)
    _PLAN_DATE = re.compile(r"\b20\d{2}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01])\b")
    _PLAN_TRAVELERS = re.compile(r"(?<!\d)[1-9]\d?\s*(?:人|travellers?|travelers?)", re.IGNORECASE)
    _PLAN_BUDGET = re.compile(r"(?:预算|budget)\s*(?:为|是|约)?\s*[:：]?\s*\d{1,8}", re.IGNORECASE)
    _PLAN_CONTEXT = re.compile(
        r"(?:规划(?:一[份个]|\d+\s*天|行程)|制定行程|生成行程|出游|想安排|"
        r"从[^，,。]{1,30}(?:出发)?\s*(?:到|去))",
        re.IGNORECASE,
    )

    def classify(self, message: str, has_trip: bool) -> IntentResult:
        normalized = message.strip().lower()
        # Existing-trip operations have the highest rule priority. In
        # particular, “第一天为什么这样安排” is an explanation, not a new
        # one-day planning request.
        if has_trip and any(term in normalized for term in ("为什么", "为何", "解释", "理由", "why", "explain")):
            return IntentResult(intent="explain_trip", confidence=1.0)
        if has_trip and any(
            term in normalized
            for term in (
                "\u6539",
                "\u8c03\u6574",
                "\u6362",
                "\u4e0d\u8981\u592a\u8d76",
                "\u522b\u592a\u8d76",
                "\u8f7b\u677e\u4e00\u70b9",
                "\u6162\u4e00\u70b9",
                "change",
                "update",
                "less rushed",
            )
        ):
            return IntentResult(intent="modify_trip", confidence=1.0)
        if any(
            term in normalized
            for term in ("作业", "裁员", "写代码", "编程", "homework", "write my")
        ):
            return IntentResult(intent="unsupported", confidence=1.0)
        # Planning context owns routing even when it also says “weather”,
        # “attractions”, or “food”: incomplete profiles must reach collection.
        if self._has_planning_context(normalized):
            return IntentResult(intent="plan_trip", confidence=1.0)
        if any(
            term in normalized
            for term in ("天气", "气温", "温度", "下雨", "降雨", "风力", "风况", "weather")
        ):
            return IntentResult(intent="weather_query", confidence=1.0)
        if any(
            term in normalized
            for term in (
                "鼓浪屿", "景点", "攻略", "怎么去", "交通", "美食", "吃什么",
                "季节", "避坑", "轮渡", "古城", "洱海",
                "旅游", "游玩", "值得去", "地方", "票价", "酒店", "排队",
                "闭馆", "民宿", "降雨量", "餐厅", "机票", "安全", "预订", "支付",
                "旅行", "出行", "换乘", "高原", "高海拔", "小吃", "特色菜",
                "雨季", "夏季", "炎热", "注意", "建议", "准备", "选择",
            )
        ):
            return IntentResult(intent="travel_knowledge", confidence=1.0)
        greeting = re.sub(r"[\s,.!?，。！？]+", "", normalized)
        if greeting in {"你好", "您好", "嗨", "哈喽", "侬好", "hello", "hi"}:
            return IntentResult(intent="smalltalk", confidence=1.0)
        return IntentResult(intent="plan_trip", confidence=1.0)

    @classmethod
    def _has_planning_context(cls, message: str) -> bool:
        return (
            cls._COMPLETE_PLAN_ROUTE.search(message) is not None
            or len(cls._PLAN_DATE.findall(message)) > 0
            or cls._PLAN_TRAVELERS.search(message) is not None
            or cls._PLAN_BUDGET.search(message) is not None
            or cls._PLAN_CONTEXT.search(message) is not None
        )


def model() -> ChatDeepSeek:
    settings = get_settings()
    if settings.deepseek_api_key is None or not settings.deepseek_api_key.get_secret_value().strip():
        raise RuntimeError("AI provider is not configured")
    return ChatDeepSeek(
        model=settings.deepseek_model,
        api_key=settings.deepseek_api_key.get_secret_value(),
        api_base=str(settings.deepseek_api_base),
        extra_body={"thinking": {"type": "disabled"}},
        temperature=0.2,
        # Keep retries explicit in Planner so every paid attempt crosses the
        # ModelGateway accounting seam and fits the two-slot reservation.
        max_retries=0,
    )


class ModelTravelExtractor:
    def extract(self, message: str, profile: TravelProfile) -> ExtractionCandidate:
        return extract_profile(message, profile)


class RuleTravelExtractor:
    """Conservatively extracts explicit profile fields without any model call."""

    _ROUTE = re.compile(
        r"(?:(?:\u4ece|from)\s*)?([^\s\uff0c,\u3002]{1,30}?)(?:\u51fa\u53d1)?\s*(?:\u5230|\u53bb|to)\s*([^\s\uff0c,\u30020-9]{1,30})",
        re.IGNORECASE,
    )
    _DATE = re.compile(
        r"(?<!\d)(20\d{2})\s*(?:[-./\u5e74])\s*(0?[1-9]|1[0-2])\s*"
        r"(?:[-./\u6708])\s*([12]\d|3[01]|0?[1-9])\s*\u65e5?"
    )
    _TRAVELERS = re.compile(r"(?<!\d)([1-9]\d?)\s*(?:\u4eba|travellers?|travelers?)", re.IGNORECASE)
    _BUDGET = re.compile(
        r"(?:\u9884\u7b97(?:\u6539\u4e3a|\u8c03\u6574\u4e3a|\u4e3a|\u662f|\u7ea6)?|budget(?:\s+(?:to|is))?)\s*[:\uff1a]?\s*(\d{1,8})",
        re.IGNORECASE,
    )

    def extract(self, message: str, profile: TravelProfile) -> TravelProfile:
        updates: dict[str, Any] = {}
        route = self._ROUTE.search(message)
        if route:
            updates.update(origin=route.group(1).strip(), destination=route.group(2).strip())
        dates = [
            f"{match.group(1)}-{int(match.group(2)):02d}-{int(match.group(3)):02d}"
            for match in self._DATE.finditer(message)
        ]
        if dates:
            updates["start_date"] = dates[0]
        if len(dates) > 1:
            updates["end_date"] = dates[1]
        travelers = self._TRAVELERS.search(message)
        if travelers:
            updates["travelers"] = int(travelers.group(1))
        budget = self._BUDGET.search(message)
        if budget:
            updates["budget_cny"] = int(budget.group(1))
        return TravelProfile.model_validate({**profile.model_dump(), **updates})


def extract_profile(
    message: str, profile: TravelProfile, *, model_factory: Any = model
) -> ExtractionCandidate:
    """Extract only explicit values, with a factory seam for offline tests."""
    schema = json.dumps(ExtractionResult.model_json_schema(), ensure_ascii=False)
    result = get_model_gateway(model_factory).invoke([
            SystemMessage(content=(
                "从用户最新消息提取旅行资料。未明确的信息不得猜测；日期为 YYYY-MM-DD，预算为人民币整数。"
                f"\nJSON Schema: {schema}\n已有资料：{profile.model_dump_json(ensure_ascii=False)}"
            )),
            HumanMessage(content=message),
    ], structured=ExtractionResult)
    return build_extraction_candidate(profile, ExtractionResult.model_validate(result).profile)


class NullEvidenceProvider:
    def fetch(self, profile: TravelProfile) -> list[TrustedEvidence]:
        return []


class ModelPlanner:
    def invoke(
        self, profile: TravelProfile, evidence: tuple[TrustedEvidence, ...]
    ) -> PlanningResult:
        response = get_model_gateway(model).invoke([
            SystemMessage(content=(
                "你是国内自由行规划助手。只能依据给定资料生成中文行程；不要声称实时价格、库存、余票或营业时间，"
                "不要执行预订或支付；无法核实的事实必须写为待确认。"
            )),
            HumanMessage(content=json.dumps({
                "profile": profile.model_dump(mode="json"),
                "allowed_evidence": [
                    {"evidence_id": item.evidence_id, "fact": item.fact}
                    for item in evidence
                ],
            }, ensure_ascii=False, indent=2)),
        ])
        return PlanningResult([PlanClaim(text=str(response.content))])


class ModelStructuredPlanner:
    """Production planner: model output is accepted only through the structured gate."""

    def __init__(self) -> None:
        from app.agent.planning import Planner

        self._planner = Planner(self._generate)

    def plan(self, profile: TravelProfile, provider_results: object):
        return self._planner.plan(profile, provider_results)

    def revise(
        self,
        profile: TravelProfile,
        provider_results: object,
        *,
        itinerary: Itinerary,
        instruction: str,
    ) -> Itinerary:
        from app.agent.planning import Planner

        return Planner(
            lambda planned_profile, results, repair_codes: self._generate(
                planned_profile,
                results,
                repair_codes,
                existing_itinerary=itinerary,
                modification_request=instruction,
            )
        ).plan(profile, provider_results)

    @staticmethod
    def _generate(
        profile: TravelProfile,
        provider_results: object,
        repair_codes: list[str] | None,
        *,
        existing_itinerary: Itinerary | None = None,
        modification_request: str | None = None,
    ) -> object:
        from app.schemas import Itinerary

        response = get_model_gateway(model).invoke([
            SystemMessage(content=(
                "Generate only one raw JSON object matching the supplied JSON Schema. "
                "Use concise, readable Chinese titles and advisory notes. Put every external fact in facts with "
                "an evidence_id; never put live prices, availability, opening hours, or source metadata in display text. "
                "The facts array may be an empty array. If a fact is included, copy both text and evidence_id exactly "
                "from one allowed_evidence entry: no translation, paraphrase, summary, combination, or invention. "
                "If no listed evidence supports a fact, omit it. If repair_codes includes CLAIM_EVIDENCE_MISMATCH, "
                "remove every fact that does not meet this exact-pair requirement."
            )),
            HumanMessage(content=json.dumps({
                "json_schema": Itinerary.model_json_schema(),
                "profile": profile.model_dump(mode="json"),
                "repair_codes": repair_codes,
                "existing_itinerary": (
                    existing_itinerary.model_dump(mode="json")
                    if existing_itinerary is not None
                    else None
                ),
                "modification_request": modification_request,
                "allowed_evidence": [
                    {"evidence_id": evidence.evidence_id, "fact": evidence.fact}
                    for evidence in _planning_evidence(provider_results)
                ],
            }, ensure_ascii=False)),
        ])
        return response.content if hasattr(response, "content") else response


class SafeTravelAgent:
    """Coordinates a bounded journey from collection through safe generation."""

    def __init__(
        self,
        classifier: IntentClassifier | None = None,
        extractor: TravelExtractor | None = None,
        planner: Any | None = None,
        repository: Any | None = None,
        usage_guard: UsageGuard | None = None,
        evidence_provider: TrustedEvidenceProvider | None = None,
        knowledge: KnowledgeAnswerer | None = None,
        weather: DailyWeatherProvider | None = None,
        initial_profile: TravelProfile | None = None,
    ) -> None:
        self._classifier = classifier or ModelIntentClassifier()
        self._extractor = extractor or ModelTravelExtractor()
        self._planner = planner or ModelStructuredPlanner()
        self._repository = repository
        self._usage_guard = usage_guard
        self._evidence_provider = evidence_provider or NullEvidenceProvider()
        self._knowledge = knowledge or UnavailableKnowledgeAnswerService()
        self._weather = weather or NullWeatherService()
        self._initial_profile = initial_profile or TravelProfile()

    def collect(self, message: str, trip: Trip | None) -> ChatResult:
        """Normalize travel details and stop before providers or the planner."""
        if _message_contains_secret(message):
            return self._sensitive_input_refusal(
                intent=_safe_rule_intent(message, trip is not None)
            )
        safety = assess_message(message)
        if safety.refused:
            return self._refusal(
                safety.code or "OUT_OF_SCOPE", intent="unsupported"
            )
        intent: Intent | None = None
        try:
            intent = self._classifier.classify(message, trip is not None).intent
            logging.getLogger("app.agent").info(
                "intent_classified", extra=operational_context(intent=intent)
            )
            special = self._special_intent_result(intent, message)
            if special is not None:
                return special
            if intent == "unsupported":
                result = self._refusal("OUT_OF_SCOPE")
                result.intent = intent
                return result
            if intent == "smalltalk":
                return ChatResult(
                    "I can help plan a 2 to 7 day domestic trip.",
                    "collecting",
                    {},
                    intent=intent,
                )
            if intent in {"modify_trip", "explain_trip"} and trip is None:
                return ChatResult(
                    "Please provide the trip details first.",
                    "collecting",
                    {},
                    intent=intent,
                )
            if intent == "explain_trip" and trip is not None:
                return self._explain_existing_trip(trip, message)
            current = trip.profile if trip is not None else self._initial_profile
            extracted = self._extractor.extract(message, current)
            candidate = (
                extracted
                if isinstance(extracted, ExtractionCandidate)
                else ExtractionCandidate(merge_profile(current, extracted))
            )
            profile = candidate.profile
            if _profile_contains_secret(profile):
                return self._sensitive_input_refusal(intent=intent)
            issues = [*candidate.issues, *validate_profile(profile)]
            missing = [name for name in REQUIRED_FIELDS if getattr(profile, name) in (None, "")]
            if issues or missing:
                return self._collecting(profile, missing, issues, intent=intent)
            destination = assess_destination(profile.destination)
            if not destination.allowed:
                if destination.code == "OUT_OF_SCOPE":
                    return self._refusal(destination.code, intent=intent)
                return ChatResult(
                    "Please confirm a specific destination in mainland China.",
                    "collecting",
                    profile.model_dump(),
                    error_code=destination.code,
                    intent=intent,
                )
            return ChatResult(
                "Trip details are complete. Confirm them to generate the itinerary.",
                "confirming",
                profile.model_dump(),
                intent=intent,
            )
        except ProviderUnavailable:
            raise
        except Exception as exc:
            logging.getLogger("app.agent").warning(
                "agent_failed",
                extra=operational_context(error_code="AGENT_UNAVAILABLE", exception_type=type(exc).__name__),
            )
            return ChatResult(
                "Unable to collect trip details right now.",
                "collecting",
                {},
                error_code="AGENT_UNAVAILABLE",
                intent=intent or _safe_rule_intent(message, trip is not None),
            )

    def plan_confirmed(
        self,
        profile: TravelProfile,
        trip: Trip | None,
        user_id: UUID | None,
        message: str,
        *,
        intent: Intent = "plan_trip",
    ) -> ChatResult:
        """Fetch evidence and plan only for a server-held validated profile."""
        del user_id
        if _message_contains_secret(message):
            return self._sensitive_input_refusal(intent=intent)
        issues = validate_profile(profile)
        missing = [name for name in REQUIRED_FIELDS if getattr(profile, name) in (None, "")]
        if issues or missing:
            return self._collecting(profile, missing, issues, intent=intent)
        destination = assess_destination(profile.destination)
        if not destination.allowed:
            return self._refusal(
                destination.code or "OUT_OF_SCOPE", intent=intent
            )
        try:
            logging.getLogger("app.agent").info(
                "planning_started",
                extra=operational_context(intent=intent),
            )
            fetched = self._evidence_provider.fetch(profile)
            provider_results = getattr(fetched, "results", fetched)
            booking_links = getattr(fetched, "booking_links", None)
            warnings = list(getattr(fetched, "warnings", ()))
            if not hasattr(self._planner, "invoke") and hasattr(self._planner, "plan"):
                from app.agent.planning import PlanValidationError, render_itinerary_markdown

                try:
                    if (
                        intent == "modify_trip"
                        and trip is not None
                        and trip.itinerary is not None
                        and hasattr(self._planner, "revise")
                    ):
                        itinerary = self._planner.revise(
                            profile,
                            provider_results,
                            itinerary=trip.itinerary,
                            instruction=message,
                        )
                    else:
                        itinerary = self._planner.plan(profile, provider_results)
                except PlanValidationError as exc:
                    logging.getLogger("app.agent").warning(
                        "plan_validation_failed",
                        extra=operational_context(
                            error_code=exc.code,
                            validation_codes=",".join(
                                sorted({issue.code for issue in exc.issues})
                            ),
                        ),
                    )
                    return ChatResult(
                        "Unable to safely validate this itinerary; please try again.",
                        "collecting",
                        profile.model_dump(),
                        error_code="PLAN_VALIDATION_FAILED",
                        warnings=warnings,
                        intent=intent,
                    )
                itinerary = enrich_itinerary(
                    itinerary,
                    destination=profile.destination or "",
                    weather=self._weather,
                    knowledge=self._knowledge,
                )
                itinerary = _attach_booking_links(itinerary, booking_links)
                citations = _itinerary_citations(itinerary)
                return ChatResult(
                    render_itinerary_markdown(itinerary),
                    "planned",
                    profile.model_dump(),
                    sources=[citation.model_dump(mode="json") for citation in citations],
                    itinerary=itinerary,
                    warnings=warnings,
                    intent=intent,
                )
            evidence = tuple(_planning_evidence(provider_results))
            result = self._verify_plan(self._planner.invoke(profile, evidence), evidence, profile)
            result.warnings = warnings
            result.intent = intent
            return result
        except ProviderUnavailable:
            raise
        except Exception as exc:
            logging.getLogger("app.agent").warning(
                "agent_failed",
                extra=operational_context(error_code="AGENT_UNAVAILABLE", exception_type=type(exc).__name__),
            )
            return ChatResult(
                "Unable to generate an itinerary right now.",
                "collecting",
                {},
                error_code="AGENT_UNAVAILABLE",
                intent=intent,
            )

    def run(self, message: str, trip: Trip | None, user_id: UUID | None = None) -> ChatResult:
        if _message_contains_secret(message):
            return self._sensitive_input_refusal(
                intent=_safe_rule_intent(message, trip is not None)
            )
        safety = assess_message(message)
        if safety.refused:
            return self._refusal(
                safety.code or "OUT_OF_SCOPE", intent="unsupported"
            )
        intent: Intent | None = None
        try:
            intent_result = self._classifier.classify(message, trip is not None)
            intent = intent_result.intent
            if intent == "unsupported":
                result = self._refusal("OUT_OF_SCOPE")
                result.intent = intent
                return result
            special = self._special_intent_result(intent, message)
            if special is not None:
                return special
            if intent == "smalltalk":
                return ChatResult(
                    "你好！我可以帮你规划国内 2 至 7 天的自由行。",
                    "collecting",
                    {},
                    intent=intent,
                )
            if intent in {"modify_trip", "explain_trip"} and trip is None:
                return ChatResult(
                    "请先告诉我出发地、目的地、日期、人数和预算，我会先帮你创建行程。",
                    "collecting",
                    {},
                    intent=intent,
                )

            current = trip.profile if trip is not None else self._initial_profile
            extracted = self._extractor.extract(message, current)
            candidate = (
                extracted
                if isinstance(extracted, ExtractionCandidate)
                else ExtractionCandidate(merge_profile(current, extracted))
            )
            profile = candidate.profile
            if _profile_contains_secret(profile):
                return self._sensitive_input_refusal(intent=intent)
            issues = [*candidate.issues, *validate_profile(profile)]
            missing = [name for name in REQUIRED_FIELDS if getattr(profile, name) in (None, "")]
            if issues or missing:
                return self._collecting(profile, missing, issues, intent=intent)
            destination = assess_destination(profile.destination)
            if not destination.allowed:
                if destination.code == "OUT_OF_SCOPE":
                    return self._refusal(destination.code, intent=intent)
                return ChatResult(
                    "请确认具体的中国境内目的地（城市或省份），我不会猜测目的地。",
                    "collecting",
                    profile.model_dump(),
                    error_code=destination.code,
                    intent=intent,
                )
            if self._usage_guard is not None and not self._usage_guard.allow(user_id):
                return ChatResult(
                    "当前规划服务暂不可用，请稍后再试。",
                    "collecting",
                    profile.model_dump(),
                    error_code="USAGE_LIMITED",
                    intent=intent,
                )

            evidence = tuple(self._evidence_provider.fetch(profile))
            if not evidence:
                result = self._unverified_framework(profile)
            elif not hasattr(self._planner, "invoke") and hasattr(self._planner, "plan"):
                from app.agent.planning import PlanValidationError, render_itinerary_markdown
                try:
                    itinerary = self._planner.plan(profile, evidence)
                except PlanValidationError:
                    result = ChatResult(
                        "Unable to safely validate this itinerary; please try again.", "collecting", profile.model_dump(),
                        error_code="PLAN_VALIDATION_FAILED",
                    )
                else:
                    itinerary = enrich_itinerary(
                        itinerary,
                        destination=profile.destination or "",
                        weather=self._weather,
                        knowledge=self._knowledge,
                    )
                    citations = _itinerary_citations(itinerary)
                    result = ChatResult(
                        render_itinerary_markdown(itinerary), "planned", profile.model_dump(),
                        sources=[citation.model_dump(mode="json") for citation in citations],
                        itinerary=itinerary,
                    )
            else:
                result = self._verify_plan(self._planner.invoke(profile, evidence), evidence, profile)
            result.intent = intent
            self._persist(trip, user_id, message, result)
            return result
        except ProviderUnavailable:
            raise
        except Exception as exc:
            logging.getLogger("app.agent").warning(
                "agent_failed",
                extra=operational_context(error_code="AGENT_UNAVAILABLE", exception_type=type(exc).__name__),
            )
            return ChatResult(
                "暂时无法生成行程，请稍后重试。",
                "collecting",
                {},
                error_code="AGENT_UNAVAILABLE",
                intent=intent or _safe_rule_intent(message, trip is not None),
            )

    @staticmethod
    def _refusal(code: str, *, intent: Intent | None = None) -> ChatResult:
        return ChatResult(
            REFUSALS[code], "collecting", {}, error_code=code, intent=intent
        )

    def _special_intent_result(self, intent: Intent, message: str) -> ChatResult | None:
        if intent == "travel_knowledge":
            region = _knowledge_region(message)
            if region is None:
                return ChatResult(
                    "请补充目的地城市，我才能查询试点旅行资料。",
                    "collecting",
                    {},
                    error_code="KNOWLEDGE_UNAVAILABLE",
                    intent=intent,
                )
            try:
                answer = self._knowledge.answer(message, region=region)
            except Exception:
                answer = RagAnswer.refused()
            return ChatResult(
                answer.reply,
                "collecting",
                {},
                sources=[item.model_dump(mode="json") for item in _rag_citations(answer)],
                intent=intent,
            )
        if intent == "weather_query":
            city = _weather_city(message)
            try:
                card = self._weather.city_card(city)
            except Exception:
                from app.schemas import WeatherCard

                card = WeatherCard(city=city, status="unavailable", summary="天气信息暂不可用")
            warnings = [card.summary] if card.status == "unavailable" else []
            if card.status == "available":
                report_time = (
                    card.report_time.strftime("%Y-%m-%d %H:%M %Z")
                    if card.report_time is not None
                    else "未知"
                )
                reply = f"实时天气（报告时间：{report_time}）：{card.summary}"
            elif card.status == "seasonal":
                reply = f"非实时天气：{card.summary}"
            else:
                reply = card.summary
            return ChatResult(reply, "collecting", {}, warnings=warnings, intent=intent)
        return None

    @staticmethod
    def _collecting(
        profile: TravelProfile,
        missing: list[str],
        issues: list[ProfileIssue],
        *,
        intent: Intent,
    ) -> ChatResult:
        if issues:
            return ChatResult(
                "请先确认以下信息：" + "；".join(issue.message for issue in issues),
                "collecting",
                profile.model_dump(),
                issues,
                error_code="PROFILE_INVALID",
                intent=intent,
            )
        labels = "、".join(FIELD_LABELS[name] for name in missing)
        return ChatResult(
            f"为了制定行程，我还需要知道：{labels}。",
            "collecting",
            profile.model_dump(),
            error_code="PROFILE_INCOMPLETE",
            intent=intent,
        )

    @staticmethod
    def _sensitive_input_refusal(intent: Intent | None = None) -> ChatResult:
        return ChatResult(
            "资料中包含疑似凭据或敏感令牌，请删除后重新提交。",
            "collecting",
            {},
            error_code="SENSITIVE_INPUT_REJECTED",
            intent=intent,
        )

    def _persist(self, trip: Trip | None, user_id: UUID | None, message: str, result: ChatResult) -> None:
        if self._repository is None or trip is None or user_id is None:
            return
        # Persistence is best-effort only after a safe response is available.
        self._repository.append_message(user_id=user_id, trip_id=trip.id, role="user", content=message)
        self._repository.append_message(user_id=user_id, trip_id=trip.id, role="assistant", content=result.reply)

    @staticmethod
    def _verify_plan(
        plan: PlanningResult | str,
        evidence: tuple[TrustedEvidence, ...],
        profile: TravelProfile,
    ) -> ChatResult:
        claims = plan.claims if isinstance(plan, PlanningResult) else [PlanClaim(text=str(plan))]
        registry = {
            item.evidence_id: item
            for item in evidence
            if _trusted_source(item)
        }
        verified = [
            (claim, registry[claim.evidence_id])
            for claim in claims
            if claim.evidence_id in registry
            and claim.text == registry[claim.evidence_id].fact
        ]
        if not verified:
            return SafeTravelAgent._unverified_framework(profile)
        return ChatResult(
            "\n".join(claim.text for claim, _ in verified), "planned", profile.model_dump(),
            sources=[
                {"url": item.source_url, "type": item.source_type, "evidence_id": item.evidence_id}
                for _, item in verified
            ],
        )

    @staticmethod
    def _unverified_framework(profile: TravelProfile) -> ChatResult:
        return ChatResult(
            "我目前只能提供建议性行程框架：请按城市区域安排每日活动。待确认：请在出发前通过官方渠道确认交通、住宿、门票和开放信息。",
            "planned", profile.model_dump(), error_code="UNVERIFIED_FACTS",
        )

    @staticmethod
    def _explain_existing_trip(trip: Trip, message: str) -> ChatResult:
        itinerary = trip.itinerary
        if itinerary is None:
            return ChatResult(
                "当前行程还没有可解释的已生成方案，请先完成规划。",
                "collecting",
                trip.profile.model_dump(),
                error_code="PLAN_REQUIRED",
                intent="explain_trip",
            )
        normalized = message.lower()
        activities = [
            (day.date, activity)
            for day in itinerary.days
            for activity in (day.morning, day.afternoon, day.evening)
        ]
        matched = [
            item
            for item in activities
            if any(
                fragment and fragment.lower() in normalized
                for fragment in re.findall(r"[\w\u4e00-\u9fff]{2,}", item[1].title)
            )
        ]
        selected = matched or activities[:3]
        lines = ["以下解释只依据已保存的行程、规划假设和带来源的事实："]
        for day_date, activity in selected[:6]:
            lines.append(
                f"- {day_date.isoformat()} {activity.start_time}–{activity.end_time}：{activity.title}"
            )
            for fact in activity.facts:
                if any(
                    citation.evidence_id == fact.evidence_id
                    and citation.fact == fact.text
                    for citation in activity.citations
                ):
                    lines.append(f"  - 已核实事实：{fact.text}")
        for assumption in itinerary.assumptions[:3]:
            lines.append(f"- 规划假设：{assumption.description}")
        citations = _itinerary_citations(itinerary)
        reply = "\n".join(lines)
        truncation = "\n\n…完整事实与来源请查看结构化行程卡。"
        if len(reply) > CHAT_REPLY_MAX_LENGTH:
            reply = reply[: CHAT_REPLY_MAX_LENGTH - len(truncation)].rstrip() + truncation
        return ChatResult(
            reply,
            "planned",
            trip.profile.model_dump(),
            sources=[citation.model_dump(mode="json") for citation in citations],
            itinerary=itinerary,
            intent="explain_trip",
        )


_chat_store = ChatSessionStore()


def chat(
    user: Any | None,
    trip_id: UUID | None,
    message: str,
    *,
    thread_id: str | None = None,
    session_scope: str | None = None,
    action: Literal["collect", "confirm"] = "collect",
) -> ChatResult:
    """Public application entry point; authenticated persistence is wired in later tasks."""
    profile = _chat_store.get(session_scope, thread_id) if thread_id and session_scope else None
    result = SafeTravelAgent(initial_profile=profile).run(message, trip=None, user_id=getattr(user, "id", None))
    if thread_id and session_scope and result.profile and result.error_code not in {"OUT_OF_SCOPE", "AGENT_UNAVAILABLE"}:
        _chat_store.put(session_scope, thread_id, TravelProfile.model_validate(result.profile))
    return result


_SENSITIVE_PROFILE_PATTERN = re.compile(
    r"(?:\bBearer\s+\S+|\beyJ[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+|"
    r"(?<![A-Za-z0-9_])(?:sk-[A-Za-z0-9_-]{20,}|sb_secret_[A-Za-z0-9_-]{20,})|"
    r"(?:api[_-]?key|password|passwd|secret|token)\s*[:=])",
    re.IGNORECASE,
)
_TRUSTED_SOURCE_TYPES = {"official", "government", "trusted_provider"}


def _profile_contains_secret(profile: TravelProfile) -> bool:
    values = [profile.origin, profile.destination, *profile.preferences, *profile.constraints]
    return any(value and _SENSITIVE_PROFILE_PATTERN.search(value) for value in values)


def _message_contains_secret(message: str) -> bool:
    return bool(_SENSITIVE_PROFILE_PATTERN.search(message))


def _safe_rule_intent(message: str, has_trip: bool) -> Intent:
    """Classify locally for logs when input must not reach a paid model."""
    return RuleIntentClassifier().classify(message, has_trip).intent


def _trusted_source(evidence: TrustedEvidence) -> bool:
    return (
        evidence.source_type in _TRUSTED_SOURCE_TYPES
        and evidence.source_url.startswith("https://")
        and ".test" not in evidence.source_url.lower()
        and bool(evidence.evidence_id.strip())
        and bool(evidence.fact.strip())
    )


def _planning_evidence(value: object) -> list[TrustedEvidence]:
    from app.providers.base import ProviderResult

    output: list[TrustedEvidence] = []
    values = value if isinstance(value, (list, tuple)) else (value,)
    for item in values:
        if isinstance(item, ProviderResult):
            output.extend(item.evidence)
        elif isinstance(item, TrustedEvidence):
            output.append(item)
    return output


def _itinerary_citations(itinerary: Any) -> list[Any]:
    citations = list(itinerary.citations)
    for day in itinerary.days:
        for activity in (day.morning, day.afternoon, day.evening):
            citations.extend(activity.citations)
    return citations


def enrich_itinerary(
    itinerary: Itinerary,
    *,
    destination: str,
    weather: DailyWeatherProvider,
    knowledge: KnowledgeAnswerer,
) -> Itinerary:
    """Attach server-produced weather without allowing a weather failure to block a plan."""
    days = []
    for day in itinerary.days:
        day_weather: ItineraryWeather | None = None
        try:
            day_weather = weather.daily_weather(destination, day.date)
        except Exception:
            day_weather = None
        if day_weather is None:
            day_weather = _seasonal_weather(destination, day.date, knowledge)
        days.append(day.model_copy(update={"weather": day_weather}))
    return itinerary.model_copy(update={"days": days})


def _seasonal_weather(
    destination: str, travel_date, knowledge: KnowledgeAnswerer
) -> ItineraryWeather | None:
    try:
        answer = knowledge.answer(
            f"{destination} 季节与避坑", region=_knowledge_region(destination)
        )
    except Exception:
        return None
    if answer.status != "grounded" or not answer.chunks:
        return None
    chunk = answer.chunks[0]
    summary = f"非实时天气：{chunk.content}【来源：{chunk.source_label}】"
    return ItineraryWeather(
        city=destination,
        status="seasonal",
        summary=summary[:500],
        date=travel_date,
    )


_TRIAL_PLACE_REGIONS: dict[str, str] = {
    "鼓浪屿": "厦门",
    "环岛路": "厦门",
    "植物园": "厦门",
    "福州": "福建",
    "三坊七巷": "福建",
    "大理": "云南",
    "丽江": "云南",
    "昆明": "云南",
    "洱海": "云南",
    "苍山": "云南",
}


def _knowledge_region(message: str) -> str | None:
    for region in ("厦门", "福建", "云南"):
        if region in message:
            return region
    matches = {
        region
        for alias, region in _TRIAL_PLACE_REGIONS.items()
        if alias in message
    }
    return next(iter(matches)) if len(matches) == 1 else None


def _weather_city(message: str) -> str:
    for city in ("厦门", "福建", "云南"):
        if city in message:
            return city
    return "未知城市"


def _rag_citations(answer: RagAnswer):
    from app.schemas import SourceCitation

    source_urls = {
        "厦门": "https://www.xm.gov.cn/",
        "福建": "https://www.fujian.gov.cn/",
        "云南": "https://www.yn.gov.cn/",
    }
    timestamp = datetime.now(UTC)
    citations = []
    for chunk in answer.chunks:
        url = next((value for key, value in source_urls.items() if key in chunk.source_label), "https://www.gov.cn/")
        citations.append(SourceCitation(
            evidence_id=f"rag:{chunk.chunk_id}",
            source_url=url,
            source_type="government",
            fetched_at=timestamp,
            freshness=f"Fetched {timestamp.isoformat()}; reference only.",
            fact=chunk.content,
            source_label=chunk.source_label,
        ))
    return citations


def _attach_booking_links(itinerary: Itinerary, booking_links: object | None) -> Itinerary:
    if booking_links is None:
        return itinerary
    from app.schemas import BookingLinks

    if isinstance(booking_links, BookingLinks):
        validated = booking_links
    elif hasattr(booking_links, "model_dump"):
        validated = BookingLinks.model_validate(booking_links.model_dump())
    else:
        validated = BookingLinks.model_validate(vars(booking_links))
    return itinerary.model_copy(update={"booking_links": validated}, deep=True)
