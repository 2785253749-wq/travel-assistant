"""Safe, testable travel-agent orchestration.

Control-flow decisions remain deterministic; models only classify/extract/generate.
"""

from __future__ import annotations

import json
import logging
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, TypedDict
from uuid import UUID

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_deepseek import ChatDeepSeek

from app.agent.extraction import merge_profile, validate_profile
from app.agent.intent import Intent, IntentResult, classify_intent
from app.agent.safety import REFUSALS, assess_destination, assess_message
from app.core.config import get_settings
from app.schemas import ExtractionResult, ProfileIssue, TravelProfile
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
    stage: Literal["collecting", "planned"]
    error_code: str | None


@dataclass
class ChatResult:
    reply: str
    stage: Literal["collecting", "planned"]
    profile: dict[str, Any]
    issues: list[ProfileIssue] = field(default_factory=list)
    sources: list[dict[str, Any]] = field(default_factory=list)
    error_code: str | None = None


@dataclass(frozen=True)
class PlanClaim:
    text: str
    source_url: str | None = None
    source_type: str | None = None
    verified: bool = False


@dataclass(frozen=True)
class PlanningResult:
    claims: list[PlanClaim]


class ChatSessionStore:
    """Bounded in-process state containing only structured travel profiles."""

    def __init__(self, max_sessions: int = 200) -> None:
        self._max_sessions = max_sessions
        self._profiles: OrderedDict[str, TravelProfile] = OrderedDict()

    def get(self, thread_id: str) -> TravelProfile | None:
        profile = self._profiles.get(thread_id)
        if profile is not None:
            self._profiles.move_to_end(thread_id)
        return profile

    def put(self, thread_id: str, profile: TravelProfile) -> None:
        self._profiles[thread_id] = TravelProfile.model_validate(profile.model_dump())
        self._profiles.move_to_end(thread_id)
        while len(self._profiles) > self._max_sessions:
            self._profiles.popitem(last=False)

    def clear(self, thread_id: str) -> None:
        self._profiles.pop(thread_id, None)


class IntentClassifier(Protocol):
    def classify(self, message: str, has_trip: bool) -> IntentResult: ...


class TravelExtractor(Protocol):
    def extract(self, message: str, profile: TravelProfile) -> TravelProfile: ...


class Planner(Protocol):
    def invoke(self, profile: TravelProfile) -> PlanningResult: ...


class UsageGuard(Protocol):
    def allow(self, user_id: UUID | None) -> bool: ...


class ModelIntentClassifier:
    def classify(self, message: str, has_trip: bool) -> IntentResult:
        return classify_intent(message, has_trip)


def model() -> ChatDeepSeek:
    settings = get_settings()
    return ChatDeepSeek(
        model=settings.deepseek_model,
        api_key=settings.deepseek_api_key.get_secret_value(),
        api_base=str(settings.deepseek_api_base),
        temperature=0.2,
        max_retries=2,
    )


class ModelTravelExtractor:
    def extract(self, message: str, profile: TravelProfile) -> TravelProfile:
        return extract_profile(message, profile)


def extract_profile(
    message: str, profile: TravelProfile, *, model_factory: Any = model
) -> TravelProfile:
    """Extract only explicit values, with a factory seam for offline tests."""
    schema = json.dumps(ExtractionResult.model_json_schema(), ensure_ascii=False)
    result = model_factory().with_structured_output(ExtractionResult, method="json_mode").invoke([
            SystemMessage(content=(
                "从用户最新消息提取旅行资料。未明确的信息不得猜测；日期为 YYYY-MM-DD，预算为人民币整数。"
                f"\nJSON Schema: {schema}\n已有资料：{profile.model_dump_json(ensure_ascii=False)}"
            )),
            HumanMessage(content=message),
    ])
    return merge_profile(profile, ExtractionResult.model_validate(result).profile)


class ModelPlanner:
    def invoke(self, profile: TravelProfile) -> PlanningResult:
        response = model().invoke([
            SystemMessage(content=(
                "你是国内自由行规划助手。只能依据给定资料生成中文行程；不要声称实时价格、库存、余票或营业时间，"
                "不要执行预订或支付；无法核实的事实必须写为待确认。"
            )),
            HumanMessage(content=profile.model_dump_json(ensure_ascii=False, indent=2)),
        ])
        return PlanningResult([PlanClaim(text=str(response.content))])


class SafeTravelAgent:
    """Coordinates a bounded journey from collection through safe generation."""

    def __init__(
        self,
        classifier: IntentClassifier | None = None,
        extractor: TravelExtractor | None = None,
        planner: Planner | None = None,
        repository: Any | None = None,
        usage_guard: UsageGuard | None = None,
        initial_profile: TravelProfile | None = None,
    ) -> None:
        self._classifier = classifier or ModelIntentClassifier()
        self._extractor = extractor or ModelTravelExtractor()
        self._planner = planner or ModelPlanner()
        self._repository = repository
        self._usage_guard = usage_guard
        self._initial_profile = initial_profile or TravelProfile()

    def run(self, message: str, trip: Trip | None, user_id: UUID | None = None) -> ChatResult:
        safety = assess_message(message)
        if safety.refused:
            return self._refusal(safety.code or "OUT_OF_SCOPE")
        try:
            intent_result = self._classifier.classify(message, trip is not None)
            intent = intent_result.intent
            if intent == "unsupported":
                return self._refusal("OUT_OF_SCOPE")
            if intent == "smalltalk":
                return ChatResult("你好！我可以帮你规划国内 2 至 7 天的自由行。", "collecting", {})
            if intent in {"modify_trip", "explain_trip"} and trip is None:
                return ChatResult("请先告诉我出发地、目的地、日期、人数和预算，我会先帮你创建行程。", "collecting", {})

            current = trip.profile if trip is not None else self._initial_profile
            profile = merge_profile(current, self._extractor.extract(message, current))
            issues = validate_profile(profile)
            missing = [name for name in REQUIRED_FIELDS if getattr(profile, name) in (None, "")]
            if issues or missing:
                return self._collecting(profile, missing, issues)
            destination = assess_destination(profile.destination)
            if not destination.allowed:
                if destination.code == "OUT_OF_SCOPE":
                    return self._refusal(destination.code)
                return ChatResult(
                    "请确认具体的中国境内目的地（城市或省份），我不会猜测目的地。",
                    "collecting", profile.model_dump(), error_code=destination.code,
                )
            if self._usage_guard is not None and not self._usage_guard.allow(user_id):
                return ChatResult("当前规划服务暂不可用，请稍后再试。", "collecting", profile.model_dump(), error_code="USAGE_LIMITED")

            result = self._verify_plan(self._planner.invoke(profile), profile)
            self._persist(trip, user_id, message, result)
            return result
        except Exception as exc:
            logging.getLogger("app.agent").warning(
                "agent_failed",
                extra={"error_code": "AGENT_UNAVAILABLE", "exception_type": type(exc).__name__},
            )
            return ChatResult("暂时无法生成行程，请稍后重试。", "collecting", {}, error_code="AGENT_UNAVAILABLE")

    @staticmethod
    def _refusal(code: str) -> ChatResult:
        return ChatResult(REFUSALS[code], "collecting", {}, error_code=code)

    @staticmethod
    def _collecting(profile: TravelProfile, missing: list[str], issues: list[ProfileIssue]) -> ChatResult:
        if issues:
            return ChatResult("请先确认以下信息：" + "；".join(issue.message for issue in issues), "collecting", profile.model_dump(), issues)
        labels = "、".join(FIELD_LABELS[name] for name in missing)
        return ChatResult(f"为了制定行程，我还需要知道：{labels}。", "collecting", profile.model_dump())

    def _persist(self, trip: Trip | None, user_id: UUID | None, message: str, result: ChatResult) -> None:
        if self._repository is None or trip is None or user_id is None:
            return
        # Persistence is best-effort only after a safe response is available.
        self._repository.append_message(user_id=user_id, trip_id=trip.id, role="user", content=message)
        self._repository.append_message(user_id=user_id, trip_id=trip.id, role="assistant", content=result.reply)

    @staticmethod
    def _verify_plan(plan: PlanningResult | str, profile: TravelProfile) -> ChatResult:
        claims = plan.claims if isinstance(plan, PlanningResult) else [PlanClaim(text=str(plan))]
        verified = [
            claim for claim in claims
            if claim.verified and claim.source_type and claim.source_url and claim.source_url.startswith(("https://", "http://"))
        ]
        if not verified:
            return ChatResult(
                "我目前只能提供建议性行程框架：请按城市区域安排每日活动。待确认：请在出发前通过官方渠道确认交通、住宿、门票和开放信息。",
                "planned", profile.model_dump(), error_code="UNVERIFIED_FACTS",
            )
        return ChatResult(
            "\n".join(claim.text for claim in verified), "planned", profile.model_dump(),
            sources=[{"url": claim.source_url, "type": claim.source_type} for claim in verified],
        )


_chat_store = ChatSessionStore()


def chat(user: Any | None, trip_id: UUID | None, message: str, *, thread_id: str | None = None) -> ChatResult:
    """Public application entry point; authenticated persistence is wired in later tasks."""
    profile = _chat_store.get(thread_id) if thread_id else None
    result = SafeTravelAgent(initial_profile=profile).run(message, trip=None, user_id=getattr(user, "id", None))
    if thread_id and result.profile and result.error_code not in {"OUT_OF_SCOPE", "AGENT_UNAVAILABLE"}:
        _chat_store.put(thread_id, TravelProfile.model_validate(result.profile))
    return result
