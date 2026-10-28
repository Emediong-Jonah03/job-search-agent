from functools import lru_cache
from urllib.parse import urlsplit

from typing import Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError


def _is_placeholder(value: str) -> bool:
    lowered = value.casefold()
    return any(marker in lowered for marker in ("replace-with", "replace-me", "change-me", "changeme", "placeholder", "your-secret"))


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: Literal["development", "test", "production"] = "development"
    app_secret_key: SecretStr
    database_url: str = "postgresql+psycopg://jobs:jobs@localhost:5432/jobs"
    redis_url: str = "redis://localhost:6379/0"
    profile_path: str = "profile.yaml"
    artifacts_path: str = "private-artifacts"
    log_level: str = "INFO"
    discovery_boards: str = ""
    follow_up_days: int = Field(default=7, ge=1, le=90)
    verification_verified_threshold: int = Field(default=70, ge=1, le=100)
    verification_suspicious_risk_threshold: int = Field(default=40, ge=1, le=1000)
    llm_provider: Literal["auto", "mock", "openai", "claude", "gemini", "groq", "xai"] = "mock"
    llm_model: str = "mock"
    llm_auto_priority: str = "xai,gemini,openai,claude,groq"
    llm_timeout_seconds: int = Field(default=45, ge=5, le=180)
    openai_api_key: SecretStr | None = None
    anthropic_api_key: SecretStr | None = None
    gemini_api_key: SecretStr | None = None
    groq_api_key: SecretStr | None = None
    xai_api_key: SecretStr | None = None
    openai_model: str = "gpt-6-luna"
    claude_model: str = "claude-sonnet-5"
    gemini_model: str = "gemini-3.8-flash"
    groq_model: str = "openai/gpt-oss-120b"
    xai_model: str = "grok-4.7"
    anthropic_api_version: str = "2023-06-01"
    dashboard_password: SecretStr | None = None

    @field_validator("dashboard_password", mode="before")
    @classmethod
    def blank_dashboard_password_is_unconfigured(cls, value: str | SecretStr | None) -> str | SecretStr | None:
        if value is None or not str(value).strip():
            return None
        return value

    @field_validator("app_secret_key")
    @classmethod
    def require_strong_session_secret(cls, value: SecretStr) -> SecretStr:
        secret = value.get_secret_value()
        if len(secret) < 32 or len(set(secret)) < 16 or _is_placeholder(secret):
            raise ValueError("APP_SECRET_KEY must be a generated, non-placeholder secret of at least 32 characters")
        return value

    @model_validator(mode="after")
    def production_dashboard_auth(self) -> "Settings":
        if self.app_env == "production" and len(self.app_secret_key.get_secret_value()) < 32:
            raise ValueError("APP_SECRET_KEY must be at least 32 characters in production")
        if self.app_env == "production" and (
            self.dashboard_password is None or len(self.dashboard_password.get_secret_value()) < 24
        ):
            raise ValueError("DASHBOARD_PASSWORD must be at least 24 characters in production")
        if self.app_env == "production":
            dashboard_password = self.dashboard_password.get_secret_value()
            if _is_placeholder(dashboard_password) or len(set(dashboard_password)) < 12:
                raise ValueError("DASHBOARD_PASSWORD must be a non-placeholder, high-entropy password in production")
            try:
                database_password = make_url(self.database_url).password
                redis_password = urlsplit(self.redis_url).password
            except (ValueError, ArgumentError) as exc:
                raise ValueError("DATABASE_URL and REDIS_URL must be valid production connection URLs") from exc
            if (
                not database_password or len(database_password) < 24
                or _is_placeholder(database_password) or database_password.casefold() in {"jobs", "password", "postgres"}
            ):
                raise ValueError("DATABASE_URL must use a unique, non-placeholder password of at least 24 characters in production")
            if (
                not redis_password or len(redis_password) < 24
                or _is_placeholder(redis_password) or redis_password.casefold() in {"jobs", "password", "redis"}
            ):
                raise ValueError("REDIS_URL must include a unique, non-placeholder password of at least 24 characters in production")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()

