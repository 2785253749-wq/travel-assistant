"""Deterministic safety boundaries for the public travel-planning agent."""

from __future__ import annotations

from dataclasses import dataclass
import re


REFUSALS = {
    "UNVERIFIABLE_REALTIME_REQUEST": "我无法核实实时价格、库存或余票，也不能代为购买。请在 12306、航空公司或酒店官方预订平台确认。",
    "OUT_OF_SCOPE": "我目前只处理国内 2 至 7 天、1 至 6 人的自由行行前规划。",
    "HIGH_STAKES_ADVICE": "签证、医疗和人身安全结论需要向相关官方机构或专业人士确认。",
}

_REALTIME_TERMS = ("余票", "库存", "实时价格", "实时票价", "保证还有", "保证有票", "帮我买", "预订", "订票", "支付")
_HIGH_STAKES_TERMS = ("签证", "医疗", "用药", "安全吗", "安全保证", "人身安全")
_OUT_OF_SCOPE_TERMS = ("出国", "境外", "国际航班", "写代码", "作业")


@dataclass(frozen=True)
class SafetyDecision:
    code: str | None = None

    @property
    def refused(self) -> bool:
        return self.code is not None


@dataclass(frozen=True)
class DestinationDecision:
    code: str | None = None

    @property
    def allowed(self) -> bool:
        return self.code is None


def assess_message(message: str) -> SafetyDecision:
    """Classify prohibited requests before any model or provider is invoked."""
    normalized = message.strip().lower()
    asks_dated_ticket_inventory = bool(
        re.search(r"(?:今天|明天|后天|\d{4}-\d{2}-\d{2}).{0,12}(?:还有|有).{0,8}(?:张|票)", normalized)
    )
    if any(term in normalized for term in _REALTIME_TERMS) or asks_dated_ticket_inventory:
        return SafetyDecision("UNVERIFIABLE_REALTIME_REQUEST")
    if any(term in normalized for term in _HIGH_STAKES_TERMS):
        return SafetyDecision("HIGH_STAKES_ADVICE")
    if any(term in normalized for term in _OUT_OF_SCOPE_TERMS):
        return SafetyDecision("OUT_OF_SCOPE")
    return SafetyDecision()


def mark_unverified(reply: str, sources: list[dict] | None = None) -> str:
    """Never present generated or unsourced dynamic facts as verified facts."""
    if sources:
        return reply
    return f"{reply}\n\n待确认：景点开放时间、交通班次、价格和库存会变化，请以官方渠道为准。"


_DOMESTIC_DESTINATIONS = (
    "北京", "上海", "天津", "重庆", "杭州", "南京", "苏州", "成都", "西安", "广州", "深圳", "厦门",
    "武汉", "长沙", "昆明", "大理", "丽江", "三亚", "青岛", "济南", "洛阳", "郑州", "哈尔滨",
    "长春", "沈阳", "福州", "泉州", "黄山", "桂林", "拉萨", "贵阳", "南宁", "海口", "澳门", "香港",
    "河北", "山西", "辽宁", "吉林", "黑龙江", "江苏", "浙江", "安徽", "福建", "江西", "山东", "河南",
    "湖北", "湖南", "广东", "海南", "四川", "贵州", "云南", "陕西", "甘肃", "青海", "台湾", "内蒙古",
    "广西", "西藏", "宁夏", "新疆", "中国", "国内",
)
_FOREIGN_DESTINATIONS = (
    "东京", "日本", "巴黎", "法国", "泰国", "曼谷", "清迈", "韩国", "首尔", "新加坡", "马来西亚",
    "美国", "纽约", "洛杉矶", "英国", "伦敦", "意大利", "罗马", "澳大利亚", "悉尼", "越南", "河内",
)


def assess_destination(destination: str | None) -> DestinationDecision:
    """Accept only an explicit domestic destination; never infer geography from a model."""
    if not destination or not destination.strip():
        return DestinationDecision("DESTINATION_UNDETERMINED")
    normalized = destination.strip()
    if any(name in normalized for name in _FOREIGN_DESTINATIONS):
        return DestinationDecision("OUT_OF_SCOPE")
    if any(name in normalized for name in _DOMESTIC_DESTINATIONS):
        return DestinationDecision()
    return DestinationDecision("DESTINATION_UNDETERMINED")
