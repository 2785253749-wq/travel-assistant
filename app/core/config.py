from functools import lru_cache
from typing import Literal
import base64

from pydantic import AnyHttpUrl, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_env: Literal["development", "test", "production"] = "development"
    log_level: str = "INFO"
    deepseek_api_key: SecretStr | None = None
    deepseek_model: str = "deepseek-v4-flash"
    deepseek_api_base: AnyHttpUrl = "https://api.deepseek.com"
    supabase_url: AnyHttpUrl | None = None
    supabase_anon_key: SecretStr | None = None
    supabase_service_key: SecretStr | None = None
    anon_session_signing_secret: SecretStr | None = None
    ai_enabled: bool = True
    ai_user_daily_limit: int = Field(default=5, ge=0, le=100)
    ai_global_daily_limit: int = Field(default=100, ge=0, le=10_000)

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @model_validator(mode="after")
    def require_production_supabase_configuration(self) -> "Settings":
        if self.app_env == "production":
            missing = [
                name
                for name, value in (
                    ("SUPABASE_URL", self.supabase_url),
                    ("SUPABASE_ANON_KEY", self.supabase_anon_key),
                    ("SUPABASE_SERVICE_KEY", self.supabase_service_key),
                )
                if self._is_missing(value)
            ]
            if not self._valid_session_secret():
                missing.append("ANON_SESSION_SIGNING_SECRET")
            if missing:
                raise ValueError(
                    "Production requires Supabase configuration: " + ", ".join(missing)
                )
        return self

    def _valid_session_secret(self) -> bool:
        if self.anon_session_signing_secret is None:
            return False
        value = self.anon_session_signing_secret.get_secret_value().strip()
        if len(value) < 43 or len(set(value)) < 8 or value.lower().startswith(("replace", "your", "example")):
            return False
        try:
            decoded = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
        except Exception:
            return False
        return len(decoded) >= 32

    @staticmethod
    def _is_missing(value: AnyHttpUrl | SecretStr | None) -> bool:
        if value is None:
            return True
        if isinstance(value, SecretStr):
            return not value.get_secret_value().strip()
        return not str(value).strip()


@lru_cache
def get_settings() -> Settings:
    return Settings()
