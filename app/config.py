from functools import lru_cache

from typing import Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: str = "development"
    app_secret_key: SecretStr
    database_url: str = "postgresql+psycopg://jobs:jobs@localhost:5432/jobs"
    redis_url: str = "redis://localhost:6379/0"
    profile_path: str = "profile.yaml"
    artifacts_path: str = "private-artifacts"
    log_level: str = "INFO"
    discovery_boards: str = ""
    verification_verified_threshold: int = Field(default=70, ge=1, le=100)
    verification_suspicious_risk_threshold: int = Field(default=40, ge=1, le=1000)
    llm_provider: Literal["mock", "claude", "gemini", "groq"] = "mock"
    llm_model: str = "mock"
    llm_timeout_seconds: int = Field(default=45, ge=5, le=180)
    anthropic_api_key: SecretStr | None = None
    gemini_api_key: SecretStr | None = None
    groq_api_key: SecretStr | None = None
    anthropic_api_version: str = "2023-06-01"
    dashboard_password: SecretStr | None = None

    @field_validator("dashboard_password", mode="before")
    @classmethod
    def blank_dashboard_password_is_unconfigured(cls, value: str | SecretStr | None) -> str | SecretStr | None:
        if value is None or not str(value).strip():
            return None
        return value

    @model_validator(mode="after")
    def production_dashboard_auth(self) -> "Settings":
        if self.app_env == "production" and len(self.app_secret_key.get_secret_value()) < 32:
            raise ValueError("APP_SECRET_KEY must be at least 32 characters in production")
        if self.app_env == "production" and (
            self.dashboard_password is None or len(self.dashboard_password.get_secret_value()) < 24
        ):
            raise ValueError("DASHBOARD_PASSWORD must be at least 24 characters in production")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()

