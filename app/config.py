from functools import lru_cache

from pydantic import SecretStr
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


@lru_cache
def get_settings() -> Settings:
    return Settings()

