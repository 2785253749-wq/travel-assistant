import json
from typing import Literal, TypedDict
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_deepseek import ChatDeepSeek
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from app.config import get_settings
from app.schemas import ExtractionResult, TravelProfile

REQUIRED = ("origin", "destination", "start_date", "end_date", "travelers", "budget_cny")
LABELS = {"origin":"出发地", "destination":"目的地", "start_date":"出发日期", "end_date":"返回日期", "travelers":"出行人数", "budget_cny":"总预算"}

class TravelState(TypedDict, total=False):
    user_message: str
    profile: dict
    missing_fields: list[str]
    reply: str
    stage: Literal["collecting", "planned"]

def model() -> ChatDeepSeek:
    settings = get_settings()
    if not settings.deepseek_api_key:
        raise RuntimeError("缺少 DEEPSEEK_API_KEY，请复制 .env.example 为 .env 并填写密钥。")
    return ChatDeepSeek(model=settings.deepseek_model, api_key=settings.deepseek_api_key,
                        api_base=settings.deepseek_api_base, temperature=0.2, max_retries=2)

def extract(state: TravelState) -> dict:
    current = TravelProfile.model_validate(state.get("profile") or {})
    schema = json.dumps(ExtractionResult.model_json_schema(), ensure_ascii=False)
    result = model().with_structured_output(
        ExtractionResult,
        method="json_mode",
    ).invoke([
        SystemMessage(content=(
            "从最新消息提取旅行资料。未明确的信息不得猜测。日期格式为YYYY-MM-DD，预算为人民币整数。"
            "只输出符合下列 JSON Schema 的 JSON 对象，不要输出其他文字。"
            f"\nJSON Schema：{schema}"
            "\n已有资料：" + current.model_dump_json(ensure_ascii=False)
        )),
        HumanMessage(content=state["user_message"]),
    ])
    merged = current.model_dump()
    for key, value in result.profile.model_dump().items():
        if value not in (None, "", []):
            merged[key] = value
    profile = TravelProfile.model_validate(merged)
    return {"profile": profile.model_dump(), "missing_fields": [k for k in REQUIRED if getattr(profile, k) in (None, "")]}

def route(state: TravelState) -> Literal["ask", "plan"]:
    return "ask" if state.get("missing_fields") else "plan"

def ask(state: TravelState) -> dict:
    return {"reply": "为了制定行程，我还需要知道：" + "、".join(LABELS[k] for k in state["missing_fields"]) + "。", "stage": "collecting"}

def plan(state: TravelState) -> dict:
    profile = TravelProfile.model_validate(state["profile"])
    response = model().invoke([SystemMessage(content=f"""你是专业旅行规划师。根据资料生成中文方案：
{profile.model_dump_json(ensure_ascii=False, indent=2)}
先给摘要和预算分配，再按天列上午、下午、晚上；考虑交通和休息。不得虚构实时价格、库存或营业时间，均标注需实时确认。最后列预订清单、风险和待确认事项。不得执行真实交易。""")])
    return {"reply": str(response.content), "stage": "planned"}

builder = StateGraph(TravelState)
builder.add_node("extract", extract); builder.add_node("ask", ask); builder.add_node("plan", plan)
builder.add_edge(START, "extract")
builder.add_conditional_edges("extract", route, {"ask":"ask", "plan":"plan"})
builder.add_edge("ask", END); builder.add_edge("plan", END)
travel_graph = builder.compile(checkpointer=InMemorySaver())

def chat(message: str, thread_id: str) -> TravelState:
    return travel_graph.invoke({"user_message": message}, {"configurable":{"thread_id":thread_id}})
