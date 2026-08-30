from typing import get_type_hints

from pydantic import SecretStr


def test_train_settings_use_server_only_secret_and_small_mvp_timeout(monkeypatch):
    from app.core.config import Settings

    monkeypatch.delenv("JUHE_TRAIN_API_KEY", raising=False)
    settings = Settings(_env_file=None)

    assert settings.juhe_train_api_key is None
    assert settings.train_timeout_seconds == 6.0
    assert get_type_hints(Settings)["juhe_train_api_key"] == SecretStr | None


def test_train_api_key_is_loaded_as_secret(monkeypatch):
    from app.core.config import Settings

    monkeypatch.setenv("JUHE_TRAIN_API_KEY", "test-train-key")

    settings = Settings(_env_file=None)

    assert isinstance(settings.juhe_train_api_key, SecretStr)
    assert settings.juhe_train_api_key.get_secret_value() == "test-train-key"


def test_train_timeout_must_be_positive():
    from app.core.config import Settings

    try:
        Settings(_env_file=None, train_timeout_seconds=0)
    except ValueError:
        return
    raise AssertionError("non-positive train timeout must be rejected")
