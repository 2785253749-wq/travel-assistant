from app.agent.graph import RuleIntentClassifier, SafeTravelAgent
from app.rag.models import RetrievedChunk
from app.rag.service import RagAnswer


class RecordingKnowledgeAnswerer:
    def __init__(self, answer: RagAnswer | None = None) -> None:
        self.calls: list[tuple[str, str | None]] = []
        self._answer = answer

    @classmethod
    def grounded(cls) -> "RecordingKnowledgeAnswerer":
        return cls(RagAnswer.grounded([
            RetrievedChunk(
                chunk_id="xiamen-gulangyu",
                content="前往鼓浪屿前应先核对码头与航线。",
                source_label="厦门市文化和旅游局公开信息",
                score=0.9,
            )
        ]))

    @classmethod
    def fail_if_called(cls) -> "RecordingKnowledgeAnswerer":
        return cls()

    def answer(self, question: str, region: str | None = None) -> RagAnswer:
        self.calls.append((question, region))
        if self._answer is None:
            raise AssertionError("知识检索不应被调用")
        return self._answer


def test_unique_trial_place_alias_routes_raw_question_to_xiamen_knowledge() -> None:
    """Removing the unique-alias route would leave an otherwise answerable question ungrounded."""
    knowledge = RecordingKnowledgeAnswerer.grounded()
    question = "鼓浪屿游玩前需要怎样安排？"

    result = SafeTravelAgent(
        classifier=RuleIntentClassifier(), knowledge=knowledge
    ).run(question, trip=None)

    assert knowledge.calls == [(question, "厦门")]
    assert result.intent == "travel_knowledge"
    assert result.sources


def test_unknown_place_refuses_without_retrieval() -> None:
    """Removing the unknown-place guard would query the pilot corpus without a destination."""
    knowledge = RecordingKnowledgeAnswerer.fail_if_called()

    result = SafeTravelAgent(
        classifier=RuleIntentClassifier(), knowledge=knowledge
    ).run("海边古城游玩前怎么安排？", trip=None)

    assert knowledge.calls == []
    assert result.error_code == "KNOWLEDGE_UNAVAILABLE"
    assert "请补充目的地城市" in result.reply
