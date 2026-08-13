from types import SimpleNamespace

from tests.evaluation import runner


def test_rag_evaluation_uses_case_question_without_region_prefix(monkeypatch) -> None:
    """Prepending evaluation metadata would mask production alias-routing failures."""
    captured: list[str] = []

    class CapturingAgent:
        def __init__(self, **_kwargs) -> None:
            pass

        def run(self, question: str, *, trip):
            captured.append(question)
            return SimpleNamespace(reply="", sources=[])

    monkeypatch.setattr(runner, "SafeTravelAgent", CapturingAgent)
    case = runner.RagWeatherCase(
        id="alias-raw-question",
        category="grounded",
        question="鼓浪屿游玩前需要怎样安排？",
        region="厦门",
        allowed_sources=[],
        expected_status="grounded",
        expected_topic="避坑",
        expected_evidence="鼓浪屿",
    )

    runner.run_rag_weather_case(
        case,
        repository=SimpleNamespace(rows=[]),
        embedder=object(),
    )

    assert captured == ["鼓浪屿游玩前需要怎样安排？"]
