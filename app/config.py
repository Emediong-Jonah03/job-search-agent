from functools import lru_cache

from pydantic import SecretStr
from pydantic import Field
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


@lru_cache
def get_settings() -> Settings:
    return Settings()

