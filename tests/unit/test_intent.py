import pytest

from app.agent.graph import RuleIntentClassifier
from app.agent.intent import IntentResult, classify_intent, route_intent


class FakeIntentModel:
    def __init__(self, result: IntentResult):
        self.result = result
        self.schema = None
        self.method = None
        self.messages = None

    def with_structured_output(self, schema, *, method):
        self.schema = schema
        self.method = method
        return self

    def invoke(self, messages):
        self.messages = messages
        return self.result


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("从上海去杭州玩三天", "plan_trip"),
        ("明天福州到上海有哪些高铁", "train_query"),
        ("把第二天改成西湖", "modify_trip"),
        ("为什么推荐灵隐寺", "explain_trip"),
        ("你好", "smalltalk"),
        ("帮我写 Java 作业", "unsupported"),
    ],
)
def test_intent_contract(message, expected):
    model = FakeIntentModel(IntentResult(intent=expected, confidence=0.9))

    result = classify_intent(message, has_trip=True, model=model)

    assert result.intent == expected
    assert model.schema is IntentResult
    assert model.method == "json_mode"
    assert model.messages[1].content == message


def test_low_confidence_intent_is_deterministically_unsupported():
    result = route_intent(IntentResult(intent="plan_trip", confidence=0.54), has_trip=True)

    assert result == "unsupported"


@pytest.mark.parametrize("intent", ["modify_trip", "explain_trip"])
def test_trip_dependent_intents_start_a_plan_when_no_trip_exists(intent):
    result = route_intent(IntentResult(intent=intent, confidence=0.9), has_trip=False)

    assert result == "plan_trip"


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("厦门今天的天气怎么样", "weather_query"),
        ("厦门去鼓浪屿怎么安排", "travel_knowledge"),
        ("明天福州到上海查车次", "train_query"),
    ],
)
def test_rule_classifier_routes_weather_and_grounded_travel_questions(message, expected):
    assert RuleIntentClassifier().classify(message, has_trip=False).intent == expected


def test_rule_classifier_routes_nearby_hotel_request_to_hotel_nearby():
    assert (
        RuleIntentClassifier()
        .classify("帮我找厦门大学附近的酒店", has_trip=False)
        .intent
        == "hotel_nearby"
    )


def test_rule_classifier_routes_bare_nearby_hotel_expression_to_hotel_nearby():
    assert (
        RuleIntentClassifier()
        .classify("鼓浪屿附近酒店", has_trip=False)
        .intent
        == "hotel_nearby"
    )


@pytest.mark.parametrize(
    "message",
    [
        "厦门大学周边酒店",
        "泉州站周围酒店",
        "厦门大学附近的酒店",
        "厦门大学周边住宿",
        "泉州站附近住哪里",
        "鼓浪屿附近有什么酒店",
        "帮我查泉州站周围的酒店",
        "推荐厦门大学附近住宿",
    ],
)
def test_rule_classifier_routes_nearby_hotel_examples_to_hotel_nearby(message):
    assert RuleIntentClassifier().classify(message, has_trip=False).intent == "hotel_nearby"


@pytest.mark.parametrize(
    "message",
    [
        "找厦门的酒店",
        "厦门大学在哪里",
        "推荐厦门景点",
        "厦门大学附近有什么好吃的",
        "厦门大学附近有什么景点",
        "我订的酒店附近有地铁吗",
        "酒店附近有地铁吗",
        "厦门酒店怎么样",
        "介绍一下厦门大学附近的酒店行业",
    ],
)
def test_rule_classifier_does_not_route_non_nearby_hotel_questions_to_hotel_nearby(message):
    assert RuleIntentClassifier().classify(message, has_trip=False).intent != "hotel_nearby"


@pytest.mark.parametrize("message", ["我订的酒店附近有地铁吗", "酒店附近有公交吗"])
def test_rule_classifier_routes_unsupported_hotel_transit_questions_to_unsupported(message):
    assert RuleIntentClassifier().classify(message, has_trip=False).intent == "unsupported"


def test_rule_classifier_keeps_hotel_restaurant_question_out_of_hotel_nearby():
    assert (
        RuleIntentClassifier()
        .classify("酒店附近有什么餐厅", has_trip=False)
        .intent
        != "hotel_nearby"
    )


def test_rule_classifier_keeps_train_queries_distinct_from_nearby_hotel_queries():
    classifier = RuleIntentClassifier()

    assert classifier.classify("查泉州到厦门的动车", has_trip=False).intent == "train_query"
    assert classifier.classify("泉州站附近住哪里", has_trip=False).intent == "hotel_nearby"


@pytest.mark.parametrize(
    "message",
    [
        "厦门有哪些酒店文化特色",
        "旅游住宿一般怎么选择",
        "厦门旅游需要注意什么",
    ],
)
def test_rule_classifier_keeps_general_hotel_knowledge_on_knowledge_route(message):
    assert RuleIntentClassifier().classify(message, has_trip=False).intent == "travel_knowledge"


@pytest.mark.parametrize(
    "message",
    [
        "从北京到厦门，2026-10-01 到 2026-10-03，2人，预算4000元，想看景点和吃美食",
        "从北京到厦门，2026-10-01 到 2026-10-03，2人，预算4000元，请把天气作为行程参考",
    ],
)
def test_rule_classifier_keeps_complete_plan_requests_on_planning_flow(message):
    assert RuleIntentClassifier().classify(message, has_trip=False).intent == "plan_trip"


@pytest.mark.parametrize(
    "message",
    [
        "明天福州到上海有哪些高铁",
        "帮我查9月10日福州到上海的车",
        "上午从福州去上海，二等座有票吗",
    ],
)
def test_rule_classifier_prioritizes_train_queries_over_planning_context(message):
    assert RuleIntentClassifier().classify(message, has_trip=False).intent == "train_query"


@pytest.mark.parametrize(
    "message",
    [
        "明天福州到上海车票有哪些",
        "福州到上海的车票",
        "帮我看看明天福州去上海还有什么车票",
        "明天福州到厦门有哪些高铁",
    ],
)
def test_rule_classifier_routes_rail_ticket_route_questions_to_train_query(message):
    assert RuleIntentClassifier().classify(message, has_trip=False).intent == "train_query"


@pytest.mark.parametrize("message", ["我要买汽车票", "大巴车票多少钱"])
def test_rule_classifier_does_not_route_road_ticket_questions_to_train_query(message):
    assert RuleIntentClassifier().classify(message, has_trip=False).intent != "train_query"


@pytest.mark.parametrize(
    "message",
    [
        "从北京到厦门，想安排景点和美食，也想参考天气",
        "厦门三天，2人预算4000元，想了解天气和景点",
    ],
)
def test_rule_classifier_keeps_partial_plan_requests_on_collection_flow(message):
    assert RuleIntentClassifier().classify(message, has_trip=False).intent == "plan_trip"


@pytest.mark.parametrize(
    "message",
    [
        "从上海往南京旅游",
        "由上海前往南京旅游",
        "上海去往南京旅游",
    ],
)
def test_rule_classifier_treats_route_synonyms_as_planning_context(message):
    assert RuleIntentClassifier().classify(message, has_trip=False).intent == "plan_trip"


@pytest.mark.parametrize("message", ["厦门未来三天天气怎么样？", "厦门三天天气如何"])
def test_rule_classifier_keeps_standalone_multi_day_weather_questions_on_weather_route(message):
    assert RuleIntentClassifier().classify(message, has_trip=False).intent == "weather_query"


@pytest.mark.parametrize(
    "message",
    [
        "厦门鼓浪屿游玩攻略",
        "厦门有哪些适合游玩的景点？",
        "福建旅游有什么避坑建议？",
        "云南有哪些值得去的地方？",
        "云南遇到雨天旅行有哪些准备建议？",
        "福建旅行时省内交通换乘如何规划？",
    ],
)
def test_common_travel_knowledge_questions_do_not_start_profile_collection(message):
    """Generic travel/visit words alone do not prove that a user is requesting a plan."""
    assert RuleIntentClassifier().classify(message, has_trip=False).intent == "travel_knowledge"
