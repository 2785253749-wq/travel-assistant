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
_REALTIME_MARKERS = ("实时", "今天", "明天", "后天", "现在", "今日")
_DYNAMIC_TRAVEL_SUBJECTS = ("机票", "航班", "酒店", "住宿", "门票", "车票", "票价", "房价")
_DYNAMIC_REQUEST_TERMS = ("价格", "票价", "多少钱", "库存", "余票", "可订", "空房", "有票", "有房")
_DYNAMIC_SUBJECT_CATEGORIES = {
    "flight": ("机票", "航班"),
    "hotel": ("酒店", "住宿", "房价"),
    "rail": ("车票",),
    "admission": ("门票",),
}
_HIGH_STAKES_GUARANTEE = re.compile(
    r"(?:保证|绝对).{0,12}(?:安全|地震|灾害|受伤|事故|风险)|不会.{0,4}(?:发生)?(?:地震|灾害|事故)"
)
_DIRECT_ENSURE_SAFETY = re.compile(
    r"确保(?:我(?:的)?|旅途|出行|行程|游客|人身|全程|大家|所有人).{0,4}(?:人身)?安全(?!装备)"
)
_PRACTICAL_SAFETY_MEASURE = re.compile(
    r"确保[^，,。；;！？!?:：\r\n]{0,16}(?:安全(?:装备|措施|检查|提示|预案)|防护措施)"
)
_DYNAMIC_LOOKUP_OPT_OUT = re.compile(
    r"(?:(?:价格|票价|房价|库存|余票|空房|可订).{0,8}(?:不用|不必|无需|别).{0,2}(?:查|查询|看|核实)"
    r"|(?:不用|不必|无需|别).{0,2}(?:查|查询|看|核实).{0,8}(?:价格|票价|房价|库存|余票|空房|可订))"
)
_DYNAMIC_LOOKUP_TARGET = re.compile(r"(?:票价|房价|价格|库存|余票|空房|可订)")
_REQUEST_CLAUSE_SEPARATOR = re.compile(r"[，,。；;！？!?:：\r\n]+")


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


@dataclass(frozen=True)
class _DynamicClauseRelations:
    positive_categories: frozenset[str]
    negated_categories: frozenset[str]
    context_categories: frozenset[str]
    has_positive_lookup: bool
    has_categoryless_positive_lookup: bool
    has_categoryless_negated_lookup: bool


def assess_message(message: str) -> SafetyDecision:
    """Classify prohibited requests before any model or provider is invoked."""
    normalized = message.strip().lower()
    train_lookup = _is_supported_train_lookup(normalized)
    asks_dated_ticket_inventory = bool(
        re.search(r"(?:今天|明天|后天|\d{4}-\d{2}-\d{2}).{0,12}(?:还有|有).{0,8}(?:张|票)", normalized)
    )
    if (
        (not train_lookup and any(term in normalized for term in _REALTIME_TERMS))
        or (not train_lookup and asks_dated_ticket_inventory)
        or (not train_lookup and _requests_realtime_dynamic_data(normalized))
    ):
        return SafetyDecision("UNVERIFIABLE_REALTIME_REQUEST")
    if (
        any(term in normalized for term in _HIGH_STAKES_TERMS)
        or _HIGH_STAKES_GUARANTEE.search(normalized)
        or _has_direct_safety_guarantee(normalized)
    ):
        return SafetyDecision("HIGH_STAKES_ADVICE")
    if any(term in normalized for term in _OUT_OF_SCOPE_TERMS):
        return SafetyDecision("OUT_OF_SCOPE")
    return SafetyDecision()


def _is_supported_train_lookup(message: str) -> bool:
    """Permit timetable/inventory lookup while retaining purchase refusals."""
    rail_terms = ("火车", "高铁", "动车", "列车", "车次", "车票", "余票", "有票")
    forbidden = ("保证", "帮我买", "购买", "支付", "预订", "订票", "抢票")
    other_dynamic_subjects = ("机票", "航班", "酒店", "住宿", "门票")
    return (
        any(term in message for term in rail_terms)
        and not any(term in message for term in forbidden)
        and not any(term in message for term in other_dynamic_subjects)
    )


def _requests_realtime_dynamic_data(message: str) -> bool:
    """Require time, travel subject, and dynamic demand in adjacent clauses."""
    clauses = _REQUEST_CLAUSE_SEPARATOR.split(message)
    for index in range(len(clauses)):
        window_clauses = clauses[index : index + 2]
        relations = [_dynamic_clause_relations(clause) for clause in window_clauses]
        window = " ".join(window_clauses)
        if (
            any(marker in window for marker in _REALTIME_MARKERS)
            and any(subject in window for subject in _DYNAMIC_TRAVEL_SUBJECTS)
            and any(relation.has_positive_lookup for relation in relations)
        ):
            if not _negated_relations_cover_window(relations):
                return True
    return False


def _dynamic_clause_relations(clause: str) -> _DynamicClauseRelations:
    """Separate positive and negated dynamic objects within one clause."""
    opt_outs = list(_DYNAMIC_LOOKUP_OPT_OUT.finditer(clause))
    negated_categories: set[str] = set()
    has_categoryless_negated_lookup = False
    for opt_out in opt_outs:
        categories = _categories_attached_to_opt_out(clause, opt_out)
        negated_categories.update(categories)
        has_categoryless_negated_lookup |= not categories

    positive_categories: set[str] = set()
    has_positive_lookup = False
    has_categoryless_positive_lookup = False
    for term in _DYNAMIC_REQUEST_TERMS:
        for demand in re.finditer(re.escape(term), clause):
            if any(_spans_overlap(demand.span(), opt_out.span()) for opt_out in opt_outs):
                continue
            has_positive_lookup = True
            segment_start = max(
                (opt_out.end() for opt_out in opt_outs if opt_out.end() <= demand.start()),
                default=0,
            )
            categories = _dynamic_subject_categories(
                [clause[segment_start : demand.end()]]
            )
            positive_categories.update(categories)
            has_categoryless_positive_lookup |= not categories

    subject_categories = _dynamic_subject_categories([clause])
    return _DynamicClauseRelations(
        positive_categories=frozenset(positive_categories),
        negated_categories=frozenset(negated_categories),
        context_categories=frozenset(subject_categories - negated_categories),
        has_positive_lookup=has_positive_lookup,
        has_categoryless_positive_lookup=has_categoryless_positive_lookup,
        has_categoryless_negated_lookup=has_categoryless_negated_lookup,
    )


def _categories_attached_to_opt_out(
    clause: str, opt_out: re.Match[str]
) -> set[str]:
    targets = [
        target
        for target in _DYNAMIC_LOOKUP_TARGET.finditer(clause)
        if opt_out.start() <= target.start() and target.end() <= opt_out.end()
    ]
    return {
        category
        for category, terms in _DYNAMIC_SUBJECT_CATEGORIES.items()
        if any(
            _subject_attaches_to_position(clause, term, opt_out.start())
            or any(_subject_attaches_to_target(clause, term, target) for target in targets)
            for term in terms
        )
    }


def _negated_relations_cover_window(relations: list[_DynamicClauseRelations]) -> bool:
    """Require negated objects to cover every positive request object."""
    request_categories = set().union(
        *(relation.positive_categories for relation in relations)
    )
    if any(relation.has_categoryless_positive_lookup for relation in relations):
        request_categories.update(
            set().union(*(relation.context_categories for relation in relations))
        )

    negated_categories = set().union(
        *(relation.negated_categories for relation in relations)
    )
    if negated_categories:
        return bool(request_categories) and request_categories <= negated_categories
    return (
        any(relation.has_categoryless_negated_lookup for relation in relations)
        and len(request_categories) <= 1
    )


def _spans_overlap(left: tuple[int, int], right: tuple[int, int]) -> bool:
    return left[0] < right[1] and right[0] < left[1]


def _subject_attaches_to_position(clause: str, term: str, position: int) -> bool:
    return any(
        subject.end() <= position
        and clause[subject.end() : position].strip() in ("", "的")
        for subject in re.finditer(re.escape(term), clause)
    )


def _subject_attaches_to_target(clause: str, term: str, target: re.Match[str]) -> bool:
    for subject in re.finditer(re.escape(term), clause):
        if subject.start() < target.end() and target.start() < subject.end():
            return True
        if subject.end() <= target.start():
            gap = clause[subject.end() : target.start()].strip()
            if gap in ("", "的"):
                return True
    return False


def _dynamic_subject_categories(clauses: list[str]) -> set[str]:
    """Map explicit travel-subject wording to stable categories."""
    return {
        category
        for category, terms in _DYNAMIC_SUBJECT_CATEGORIES.items()
        if any(term in clause for clause in clauses for term in terms)
    }


def _has_direct_safety_guarantee(message: str) -> bool:
    """Ignore only direct-safety matches covered by a practical precaution."""
    practical_spans = [match.span() for match in _PRACTICAL_SAFETY_MEASURE.finditer(message)]
    return any(
        not any(
            practical_start <= direct.start() and direct.end() <= practical_end
            for practical_start, practical_end in practical_spans
        )
        for direct in _DIRECT_ENSURE_SAFETY.finditer(message)
    )


def mark_unverified(reply: str, sources: list[dict] | None = None) -> str:
    """Never present generated or unsourced dynamic facts as verified facts."""
    if sources:
        return reply
    return f"{reply}\n\n待确认：景点开放时间、交通班次、价格和库存会变化，请以官方渠道为准。"


_DOMESTIC_DESTINATIONS = (
    "北京", "上海", "天津", "重庆", "杭州", "南京", "苏州", "成都", "西安", "广州", "深圳", "厦门",
    "武汉", "长沙", "昆明", "大理", "丽江", "三亚", "青岛", "济南", "洛阳", "郑州", "哈尔滨", "兰州", "兰州市", "西宁", "西宁市",
    "长春", "沈阳", "福州", "泉州", "黄山", "桂林", "拉萨", "贵阳", "南宁", "海口",
    "河北", "山西", "辽宁", "吉林", "黑龙江", "江苏", "浙江", "安徽", "福建", "江西", "山东", "河南",
    "湖北", "湖南", "广东", "海南", "四川", "贵州", "云南", "陕西", "甘肃", "青海", "内蒙古",
    "广西", "西藏", "宁夏", "新疆", "中国大陆", "国内",
)
_FOREIGN_DESTINATIONS = (
    "阿富汗", "阿尔巴尼亚", "阿尔及利亚", "安道尔", "安哥拉", "安提瓜和巴布达", "阿根廷", "亚美尼亚",
    "澳大利亚", "奥地利", "阿塞拜疆", "巴哈马", "巴林", "孟加拉国", "巴巴多斯", "白俄罗斯",
    "比利时", "伯利兹", "贝宁", "不丹", "玻利维亚", "波黑", "博茨瓦纳", "巴西", "文莱", "保加利亚",
    "布基纳法索", "布隆迪", "柬埔寨", "喀麦隆", "加拿大", "佛得角", "中非", "乍得", "智利",
    "哥伦比亚", "科摩罗", "刚果", "哥斯达黎加", "克罗地亚", "古巴", "塞浦路斯", "捷克", "丹麦",
    "吉布提", "多米尼克", "多米尼加", "厄瓜多尔", "埃及", "萨尔瓦多", "赤道几内亚", "厄立特里亚",
    "爱沙尼亚", "斯威士兰", "埃塞俄比亚", "斐济", "芬兰", "法国", "加蓬", "冈比亚", "格鲁吉亚",
    "德国", "加纳", "希腊", "格林纳达", "危地马拉", "几内亚", "圭亚那", "海地", "洪都拉斯",
    "匈牙利", "冰岛", "印度", "印度尼西亚", "伊朗", "伊拉克", "爱尔兰", "以色列", "意大利",
    "牙买加", "日本", "约旦", "哈萨克斯坦", "肯尼亚", "基里巴斯", "朝鲜", "韩国", "科威特",
    "吉尔吉斯斯坦", "老挝", "拉脱维亚", "黎巴嫩", "莱索托", "利比里亚", "利比亚", "列支敦士登",
    "立陶宛", "卢森堡", "马达加斯加", "马拉维", "马来西亚", "马尔代夫", "马里", "马耳他",
    "马绍尔群岛", "毛里塔尼亚", "毛里求斯", "墨西哥", "密克罗尼西亚", "摩尔多瓦", "摩纳哥",
    "蒙古", "黑山", "摩洛哥", "莫桑比克", "缅甸", "纳米比亚", "瑙鲁", "尼泊尔", "荷兰",
    "新西兰", "尼加拉瓜", "尼日尔", "尼日利亚", "北马其顿", "挪威", "阿曼", "巴基斯坦",
    "帕劳", "巴拿马", "巴布亚新几内亚", "巴拉圭", "秘鲁", "菲律宾", "波兰", "葡萄牙", "卡塔尔",
    "罗马尼亚", "俄罗斯", "卢旺达", "圣基茨和尼维斯", "圣卢西亚", "圣文森特和格林纳丁斯",
    "萨摩亚", "圣马力诺", "圣多美和普林西比", "沙特阿拉伯", "塞内加尔", "塞尔维亚", "塞舌尔",
    "塞拉利昂", "新加坡", "斯洛伐克", "斯洛文尼亚", "所罗门群岛", "索马里", "南非", "南苏丹",
    "西班牙", "斯里兰卡", "苏丹", "苏里南", "瑞典", "瑞士", "叙利亚", "塔吉克斯坦", "坦桑尼亚",
    "泰国", "东帝汶", "多哥", "汤加", "特立尼达和多巴哥", "突尼斯", "土耳其", "土库曼斯坦",
    "图瓦卢", "乌干达", "乌克兰", "阿联酋", "英国", "美国", "乌拉圭", "乌兹别克斯坦",
    "瓦努阿图", "梵蒂冈", "委内瑞拉", "越南", "也门", "赞比亚", "津巴布韦",
    "东京", "巴黎", "曼谷", "清迈", "首尔", "纽约", "洛杉矶", "伦敦", "罗马", "悉尼", "河内",
    "柏林", "慕尼黑", "法兰克福", "大阪", "京都", "新德里", "迪拜", "莫斯科", "马德里",
)
_NON_MAINLAND_DESTINATIONS = ("香港", "澳门", "台湾")


def assess_destination(destination: str | None) -> DestinationDecision:
    """Accept only an explicit domestic destination; never infer geography from a model."""
    if not destination or not destination.strip():
        return DestinationDecision("DESTINATION_UNDETERMINED")
    normalized = destination.strip()
    if any(name in normalized for name in (*_FOREIGN_DESTINATIONS, *_NON_MAINLAND_DESTINATIONS)):
        return DestinationDecision("OUT_OF_SCOPE")
    if any(name in normalized for name in _DOMESTIC_DESTINATIONS):
        return DestinationDecision()
    return DestinationDecision("DESTINATION_UNDETERMINED")
