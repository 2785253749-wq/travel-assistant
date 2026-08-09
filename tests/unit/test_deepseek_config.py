from pydantic import SecretStr

from app.agent import graph, intent
from app.core.config import Settings


def _settings() -> Settings:
    return Settings(deepseek_api_key=SecretStr("test-key"))


def test_all_deepseek_clients_disable_thinking_for_structured_json(monkeypatch):
    monkeypatch.setattr(intent, "get_settings", _settings)
    monkeypatch.setattr(graph, "get_settings", _settings)

    assert intent.intent_model().extra_body == {"thinking": {"type": "disabled"}}
    assert graph.model().extra_body == {"thinking": {"type": "disabled"}}
