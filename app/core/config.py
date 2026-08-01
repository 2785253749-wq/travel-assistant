from functools import lru_cache
from typing import Literal
import base64
from collections import Counter
from math import log2
import re

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
    trusted_client_ip_header: Literal["none", "cf-connecting-ip"] = "none"

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
        value = self.anon_session_signing_secret.get_secret_value()
        if not re.fullmatch(r"[A-Za-z0-9_-]{43,}", value):
            return False
        if value.lower().startswith(("replace", "your", "example", "test")):
            return False
        try:
            decoded = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
        except Exception:
            return False
        canonical = base64.urlsafe_b64encode(decoded).rstrip(b"=").decode("ascii")
        if canonical != value or len(decoded) < 32:
            return False
        if any(
            len(decoded) % period == 0
            and decoded == decoded[:period] * (len(decoded) // period)
            for period in range(1, len(decoded) // 2 + 1)
        ):
            return False
        frequencies = Counter(decoded)
        total_entropy_bits = sum(
            -count * log2(count / len(decoded)) for count in frequencies.values()
        )
        if total_entropy_bits < 128:
            return False
        decoded_lower = decoded.lower()
        if any(
            placeholder in decoded_lower
            for placeholder in (
                b"replace",
                b"change-me",
                b"changeme",
                b"your-secret",
                b"example",
                b"placeholder",
                b"test-secret",
                b"secret-key",
            )
        ):
            return False
        return True

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
