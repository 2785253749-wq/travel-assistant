"""Fixed offline model responses keyed only by raw user input.

This file intentionally does not import evaluation cases or their expected
answers.  It is the sole model/provider fixture source for the offline gate.
"""

from __future__ import annotations

from typing import Any


class FixtureHttpError(Exception):
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code


_PLANS: tuple[tuple[str, dict[str, Any]], ...] = (
    ("上海到杭州两天一人慢游，预算1800", {"origin":"上海","destination":"杭州","start_date":"2026-10-01","end_date":"2026-10-02","travelers":1,"budget_cny":1800,"preferences":["慢游"]}),
    ("北京去西安三天两人，预算3500，历史景点", {"origin":"北京","destination":"西安","start_date":"2026-10-02","end_date":"2026-10-04","travelers":2,"budget_cny":3500,"preferences":["历史"]}),
    ("广州到厦门五天四人亲子游，预算9000", {"origin":"广州","destination":"厦门","start_date":"2026-10-03","end_date":"2026-10-07","travelers":4,"budget_cny":9000,"preferences":["亲子"]}),
    ("成都去昆明七天六人美食游，预算16000", {"origin":"成都","destination":"昆明","start_date":"2026-10-04","end_date":"2026-10-10","travelers":6,"budget_cny":16000,"preferences":["美食"]}),
    ("南京到苏州周末两天两人，预算2400，园林", {"origin":"南京","destination":"苏州","start_date":"2026-10-05","end_date":"2026-10-06","travelers":2,"budget_cny":2400,"preferences":["园林"]}),
    ("武汉到长沙三天一人，预算2200，夜市", {"origin":"武汉","destination":"长沙","start_date":"2026-10-06","end_date":"2026-10-08","travelers":1,"budget_cny":2200,"preferences":["夜市"]}),
    ("深圳到桂林五天两人摄影，预算6000", {"origin":"深圳","destination":"桂林","start_date":"2026-10-07","end_date":"2026-10-11","travelers":2,"budget_cny":6000,"preferences":["摄影"]}),
    ("郑州去青岛七天四人海边，预算11000", {"origin":"郑州","destination":"青岛","start_date":"2026-10-08","end_date":"2026-10-14","travelers":4,"budget_cny":11000,"preferences":["海边"]}),
    ("福州到黄山两天六人登山，预算5000", {"origin":"福州","destination":"黄山","start_date":"2026-10-09","end_date":"2026-10-10","travelers":6,"budget_cny":5000,"preferences":["登山"]}),
    ("济南去大理三天两人低预算，2800", {"origin":"济南","destination":"大理","start_date":"2026-10-10","end_date":"2026-10-12","travelers":2,"budget_cny":2800,"preferences":["低预算"]}),
    ("天津到丽江五天一人高预算，9000", {"origin":"天津","destination":"丽江","start_date":"2026-10-11","end_date":"2026-10-15","travelers":1,"budget_cny":9000,"preferences":["舒适"]}),
    ("重庆到三亚七天四人度假，预算18000", {"origin":"重庆","destination":"三亚","start_date":"2026-10-12","end_date":"2026-10-18","travelers":4,"budget_cny":18000,"preferences":["度假"]}),
    ("上海到北京两天六人博物馆，预算12000", {"origin":"上海","destination":"北京","start_date":"2026-10-13","end_date":"2026-10-14","travelers":6,"budget_cny":12000,"preferences":["博物馆"]}),
    ("杭州去成都三天两人熊猫，预算4800", {"origin":"杭州","destination":"成都","start_date":"2026-10-14","end_date":"2026-10-16","travelers":2,"budget_cny":4800,"preferences":["熊猫"]}),
    ("西安到兰州五天一人文化游，预算4000", {"origin":"西安","destination":"兰州","start_date":"2026-10-15","end_date":"2026-10-19","travelers":1,"budget_cny":4000,"preferences":["文化"]}),
    ("贵阳到南宁七天四人自驾，预算10000", {"origin":"贵阳","destination":"南宁","start_date":"2026-10-16","end_date":"2026-10-22","travelers":4,"budget_cny":10000,"preferences":["自驾"]}),
    ("海口到广州两天六人美食，预算7000", {"origin":"海口","destination":"广州","start_date":"2026-10-17","end_date":"2026-10-18","travelers":6,"budget_cny":7000,"preferences":["美食"]}),
    ("哈尔滨到沈阳三天两人冬季，预算3600", {"origin":"哈尔滨","destination":"沈阳","start_date":"2026-10-18","end_date":"2026-10-20","travelers":2,"budget_cny":3600,"preferences":["冬季"]}),
    ("拉萨到西宁五天一人自然，预算6500", {"origin":"拉萨","destination":"西宁","start_date":"2026-10-19","end_date":"2026-10-23","travelers":1,"budget_cny":6500,"preferences":["自然"]}),
    ("昆明到杭州七天四人无障碍，预算14000", {"origin":"昆明","destination":"杭州","start_date":"2026-10-20","end_date":"2026-10-26","travelers":4,"budget_cny":14000,"constraints":["无障碍"]}),
)

PROFILE_BY_MESSAGE = {message: profile for message, profile in _PLANS}
SOURCE_IDS_BY_MESSAGE = {message: [f"p{index:03d}"] for index, (message, _) in enumerate(_PLANS, start=1)}
PROFILE_BY_MESSAGE.update({
    "从上海出发玩三天": {"origin":"上海"},
    "去杭州玩，日期还没定": {"destination":"杭州"},
    "北京到西安，10月5日回来、10月8日出发": {"origin":"北京","destination":"西安","start_date":"2026-10-08","end_date":"2026-10-05"},
    "上海去苏州，0个人，预算2000": {"origin":"上海","destination":"苏州","travelers":0,"budget_cny":2000},
    "上海去苏州，7个人，预算2000": {"origin":"上海","destination":"苏州","travelers":7,"budget_cny":2000},
    "广州到厦门两天两人，预算没想好": {"origin":"广州","destination":"厦门","travelers":2},
    "明天从成都去重庆，回来日期不确定": {"origin":"成都","destination":"重庆","start_date":"2026-07-29"},
    "杭州两天，两人": {"destination":"杭州","travelers":2},
    "上海出发；去杭州；10月1到3日；两个人": {"origin":"上海","destination":"杭州","start_date":"2026-10-01","end_date":"2026-10-03","travelers":2},
    "出发地上海，目的地也上海，住几天再说": {"origin":"上海","destination":"上海"},
    "预算1000到3000之间，去北京": {"destination":"北京"},
    "从武汉去长沙，日期2026/10/1-2026/10/3": {"origin":"武汉","destination":"长沙","start_date":"2026-10-01","end_date":"2026-10-03"},
    "两人从南京去苏州，想吃什么都行": {"origin":"南京","destination":"苏州","travelers":2},
    "把第二天改成西湖": {"preferences":["第二天西湖"]},
    "行程改成两个人": {"travelers":2},
    "把预算改为5000": {"budget_cny":5000},
    "不要早起，改晚一点": {"preferences":["晚一点出发"]},
    "把酒店偏好换成地铁附近": {"preferences":["地铁附近酒店"]},
    "魔都到苏州，周末两日两人": {"origin":"上海","destination":"苏州","start_date":"2026-08-01","end_date":"2026-08-02","travelers":2},
    "下周末去那儿，预算三千": {"start_date":"2026-08-08","end_date":"2026-08-09","budget_cny":3000},
    "上次那个杭州行程，第二天换西湖": {"preferences":["第二天西湖"]},
    "Shanghai to Hangzhou, two days, two people": {"origin":"上海","destination":"杭州","travelers":2},
    "俺也去成都吃火锅，啥时候合适": {"destination":"成都","preferences":["火锅"]},
    "去西安玩儿，预算四千，日期待定": {"destination":"西安","budget_cny":4000},
    "帮俺整一个青岛海边路线": {"destination":"青岛","preferences":["海边"]},
    "the second day不要太赶": {"constraints":["第二天不要太赶"]},
    "明儿从北京走，后天回，俩人": {"origin":"北京","start_date":"2026-07-29","end_date":"2026-07-30","travelers":2},
    "杭州西湖附近，two pax，budget 3k": {"destination":"杭州","travelers":2,"budget_cny":3000,"preferences":["西湖附近"]},
    "把day 2改成museum": {"preferences":["第二天博物馆"]},
    "北平到西安，来回三天，预算伍仟": {"origin":"北京","destination":"西安","budget_cny":5000},
    "上海到杭州两天两人3000": {"origin":"上海","destination":"杭州","start_date":"2026-10-01","end_date":"2026-10-02","travelers":2,"budget_cny":3000},
    "北京到西安三天两人4000": {"origin":"北京","destination":"西安","start_date":"2026-10-01","end_date":"2026-10-03","travelers":2,"budget_cny":4000},
    "广州到厦门三天两人4000": {"origin":"广州","destination":"厦门","start_date":"2026-10-01","end_date":"2026-10-03","travelers":2,"budget_cny":4000},
    "成都到重庆两天两人3000": {"origin":"成都","destination":"重庆","start_date":"2026-10-01","end_date":"2026-10-02","travelers":2,"budget_cny":3000},
    "南京到苏州两天两人2400": {"origin":"南京","destination":"苏州","start_date":"2026-10-01","end_date":"2026-10-02","travelers":2,"budget_cny":2400},
    "武汉到长沙三天两人2800": {"origin":"武汉","destination":"长沙","start_date":"2026-10-01","end_date":"2026-10-03","travelers":2,"budget_cny":2800},
    "深圳到桂林三天两人5000": {"origin":"深圳","destination":"桂林","start_date":"2026-10-01","end_date":"2026-10-03","travelers":2,"budget_cny":5000},
    "郑州到青岛三天两人4200": {"origin":"郑州","destination":"青岛","start_date":"2026-10-01","end_date":"2026-10-03","travelers":2,"budget_cny":4200},
    "福州到黄山两天两人3600": {"origin":"福州","destination":"黄山","start_date":"2026-10-01","end_date":"2026-10-02","travelers":2,"budget_cny":3600},
    "济南到大理三天两人5000": {"origin":"济南","destination":"大理","start_date":"2026-10-01","end_date":"2026-10-03","travelers":2,"budget_cny":5000},
})
SOURCE_IDS_BY_MESSAGE.update({
    "把第二天改成西湖": ["m016"], "行程改成两个人": ["m017"], "把预算改为5000": ["m018"],
    "不要早起，改晚一点": ["m019"], "把酒店偏好换成地铁附近": ["m020"],
    "上次那个杭州行程，第二天换西湖": ["p001"], "the second day不要太赶": ["p001"], "把day 2改成museum": ["p001"],
    "福州到黄山两天两人3600": ["e009"],
})


def source_ids_for(message: str) -> list[str]:
    return list(SOURCE_IDS_BY_MESSAGE.get(message, []))
INTENT_BY_MESSAGE = {message: "plan_trip" for message, _ in _PLANS}
INTENT_BY_MESSAGE.update({
    "把第二天改成西湖": "modify_trip", "行程改成两个人": "modify_trip", "把预算改为5000": "modify_trip",
    "不要早起，改晚一点": "modify_trip", "把酒店偏好换成地铁附近": "modify_trip",
    "上次那个杭州行程，第二天换西湖": "modify_trip", "the second day不要太赶": "modify_trip", "把day 2改成museum": "modify_trip",
    "侬好，想去苏州白相": "smalltalk",
})

_ERROR_STATUS = {"成都到重庆两天两人3000": 400, "南京到苏州两天两人2400": 429, "武汉到长沙三天两人2800": 500}
SCENARIO_BY_MESSAGE = {
    "上海到杭州两天两人3000": "weather_timeout", "北京到西安三天两人4000": "places_empty_retry",
    "广州到厦门三天两人4000": "circuit_open", "成都到重庆两天两人3000": "model_400",
    "南京到苏州两天两人2400": "model_429", "武汉到长沙三天两人2800": "model_500",
    "深圳到桂林三天两人5000": "user_limit", "郑州到青岛三天两人4200": "kill_switch",
    "福州到黄山两天两人3600": "format_twice", "济南到大理三天两人5000": "database_failure",
}
ERROR_BY_SCENARIO = {
    "weather_timeout": "WEATHER_TIMEOUT", "places_empty_retry": "PLACES_EMPTY_AFTER_RETRY",
    "model_400": "AI_UNAVAILABLE", "model_429": "AI_RATE_LIMITED", "model_500": "AI_UNAVAILABLE",
    "user_limit": "AI_DAILY_LIMIT_REACHED", "global_limit": "AI_GLOBAL_DAILY_LIMIT_REACHED",
    "kill_switch": "AI_DISABLED", "circuit_open": "AI_CIRCUIT_OPEN",
    "format_twice": "PLAN_VALIDATION_FAILED", "database_failure": "DATABASE_FAILURE",
}


class OfflineModel:
    """A JSON-mode compatible model double. No expected value is consulted."""

    def with_structured_output(self, schema: object, method: str = "json_mode") -> "OfflineModel":
        return self

    def invoke(self, messages: list[object]) -> dict[str, Any]:
        message = str(getattr(messages[-1], "content", ""))
        if message in _ERROR_STATUS:
            raise FixtureHttpError(_ERROR_STATUS[message])
        system = str(getattr(messages[0], "content", ""))
        if "intent" in system.lower() or "意图" in system:
            return {"intent": self.intent_for(message), "confidence": 0.99}
        return {"profile": self.profile_for(message)}

    @staticmethod
    def intent_for(message: str) -> str:
        if message in INTENT_BY_MESSAGE:
            return INTENT_BY_MESSAGE[message]
        if any(term in message for term in ("余票", "实时", "签证", "医疗", "安全", "作业", "东京", "香港", "预订", "库存", "价格", "忽略之前", "断言", "退改签", "裁员")):
            return "unsupported"
        if "改" in message or "第二天" in message:
            return "modify_trip"
        return "plan_trip"

    @staticmethod
    def profile_for(message: str) -> dict[str, Any]:
        if message in PROFILE_BY_MESSAGE:
            return PROFILE_BY_MESSAGE[message]
        return {}


def model_factory() -> OfflineModel:
    return OfflineModel()
