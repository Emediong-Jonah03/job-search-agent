from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator


class DiscoveredJob(BaseModel):
    model_config = ConfigDict(extra="forbid")

    employer: str = Field(min_length=1, max_length=200)
    title: str = Field(min_length=1, max_length=200)
    original_url: HttpUrl
    source_url: HttpUrl
    description: str = ""
    posted_at: datetime | None = None
    location: str = ""
    workplace_type: str = ""
    salary_text: str = ""
    source_name: str
    source_id: str
    raw_metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("posted_at")
    @classmethod
    def timestamps_are_timezone_aware(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value


class BoardConfig(BaseModel):
    source: str
    board: str


class LinkCheck(BaseModel):
    url: str
    accessible: bool
    status_code: int | None = None
    reason: str
    checked_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

