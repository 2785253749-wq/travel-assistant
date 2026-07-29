from typing import Any, Literal

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_deepseek import ChatDeepSeek
from pydantic import BaseModel, Field

from app.core.config import get_settings
from app.core.usage import ModelGateway, get_model_gateway

Intent = Literal[
    "plan_trip", "modify_trip", "explain_trip", "smalltalk", "unsupported"
]


class IntentResult(BaseModel):
    intent: Intent
    confidence: float = Field(ge=0, le=1)


def intent_model() -> ChatDeepSeek:
    settings = get_settings()
    return ChatDeepSeek(
        model=settings.deepseek_model,
        api_key=settings.deepseek_api_key.get_secret_value(),
        api_base=str(settings.deepseek_api_base),
        temperature=0,
        max_retries=2,
    )


def route_intent(result: IntentResult, has_trip: bool) -> Intent:
    if result.intent in {"modify_trip", "explain_trip"} and not has_trip:
        return "plan_trip"
    if result.confidence < 0.55:
        return "unsupported"
    return result.intent


def classify_intent(message: str, has_trip: bool, model: Any | None = None, gateway: ModelGateway | None = None) -> IntentResult:
    controlled = gateway or (ModelGateway(lambda: model) if model is not None else get_model_gateway(intent_model))
    result = IntentResult.model_validate(controlled.invoke([
        SystemMessage(content=_INTENT_PROMPT), HumanMessage(content=message),
    ], structured=IntentResult))
    return result.model_copy(update={"intent": route_intent(result, has_trip)})


_INTENT_PROMPT = """你只负责识别用户消息的意图，并返回符合 JSON Schema 的结果。
可选意图只有：
- plan_trip：开始规划中国境内 2 至 7 天、1 至 6 人的自由行，或补充这类行程资料。
- modify_trip：修改已有行程的内容。
- explain_trip：解释已有行程的推荐或安排。
- smalltalk：问候、闲聊且不要求旅行服务。
- unsupported：任何非国内自由行需求，包括作业、代码、跨境旅行、预订或支付。
只分类；不要生成行程、事实、建议或最终用户回复。信息不明确时降低 confidence。"""
