from fastapi.testclient import TestClient
from app.main import app
from app.schemas import ExtractionResult, TravelProfile

def test_health():
    assert TestClient(app).get("/health").json() == {"status":"ok"}

def test_home():
    response = TestClient(app).get("/")
    assert response.status_code == 200 and "旅行助手" in response.text

def test_extract_uses_json_mode(monkeypatch):
    from app import graph

    class StructuredModel:
        def invoke(self, messages):
            assert "JSON Schema" in messages[0].content
            return ExtractionResult(
                profile=TravelProfile(origin="上海", destination="杭州")
            )

    class FakeModel:
        def with_structured_output(self, schema, *, method):
            assert schema is ExtractionResult
            assert method == "json_mode"
            return StructuredModel()

    monkeypatch.setattr(graph, "model", lambda: FakeModel())
    result = graph.extract({"user_message": "从上海去杭州"})
    assert result["profile"]["origin"] == "上海"
    assert result["profile"]["destination"] == "杭州"
