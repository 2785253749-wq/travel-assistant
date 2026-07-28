from functools import lru_cache
from typing import Literal

from pydantic import AnyHttpUrl, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_env: Literal["development", "test", "production"] = "development"
    log_level: str = "INFO"
    deepseek_api_key: SecretStr
    deepseek_model: str = "deepseek-v4-flash"
    deepseek_api_base: AnyHttpUrl = "https://api.deepseek.com"
    supabase_url: AnyHttpUrl | None = None
    supabase_anon_key: SecretStr | None = None
    supabase_service_key: SecretStr | None = None
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
            if missing:
                raise ValueError(
                    "Production requires Supabase configuration: " + ", ".join(missing)
                )
        return self

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
