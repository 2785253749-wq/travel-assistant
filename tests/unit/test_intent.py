import pytest

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
