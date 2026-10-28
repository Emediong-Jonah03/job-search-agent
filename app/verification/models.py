from datetime import datetime, timezone
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, HttpUrl

from app.domain.models import VerificationClassification


class SignalKind(StrEnum):
    POSITIVE = "POSITIVE"
    RISK = "RISK"


class VerificationEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    employer: str
    title: str
    original_url: HttpUrl
    source_url: HttpUrl
    source_name: str
    description: str = ""
    posted_at: datetime | None = None
    link_accessible: bool | None = None
    observed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class VerificationSignal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    kind: SignalKind
    weight: int = Field(ge=0, le=100)
    finding: str
    evidence_url: HttpUrl | None = None
    observed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    data_source: str
    reliability: float = Field(ge=0, le=1)


class VerificationAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    classification: VerificationClassification
    score: int = Field(ge=0, le=100)
    support_score: int = Field(ge=0, le=100)
    risk_score: int = Field(ge=0, le=1000)
    signals: list[VerificationSignal]
    reasons: list[str]
    missing_evidence: list[str]
    uncertainty: list[str]
    policy_version: str
    policy_parameters: dict[str, int]
    assessed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

