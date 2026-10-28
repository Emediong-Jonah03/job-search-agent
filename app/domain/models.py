from datetime import date
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator


class ApplicationState(StrEnum):
    DISCOVERED = "DISCOVERED"
    VERIFIED = "VERIFIED"
    SHORTLISTED = "SHORTLISTED"
    CV_GENERATED = "CV_GENERATED"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    APPROVED = "APPROVED"
    SUBMITTED = "SUBMITTED"
    FOLLOW_UP = "FOLLOW_UP"
    REJECTED_BY_VERIFICATION = "REJECTED_BY_VERIFICATION"
    SKIPPED = "SKIPPED"


class SubmissionStatus(StrEnum):
    NOT_ATTEMPTED = "NOT_ATTEMPTED"
    IN_PROGRESS = "IN_PROGRESS"
    CONFIRMED_SUCCESS = "CONFIRMED_SUCCESS"
    CONFIRMED_FAILURE = "CONFIRMED_FAILURE"
    OUTCOME_UNKNOWN = "OUTCOME_UNKNOWN"


class VerificationClassification(StrEnum):
    VERIFIED = "VERIFIED"
    PROBABLE = "PROBABLE"
    SUSPICIOUS = "SUSPICIOUS"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class JobRecord(StrictModel):
    employer: str = Field(min_length=1, max_length=200)
    title: str = Field(min_length=1, max_length=200)
    canonical_url: HttpUrl
    posted_at: date | None = None
    discovered_at: date
    description: str = ""


class AuditEvent(StrictModel):
    event_type: str = Field(min_length=1, max_length=100)
    actor: str = Field(min_length=1, max_length=200)
    occurred_at: date
    application_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class StateTransitionRequest(StrictModel):
    target: ApplicationState
    actor: str = Field(min_length=1, max_length=200)
    reason: str | None = Field(default=None, max_length=1000)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ProfileSkill(StrictModel):
    name: str = Field(min_length=1, max_length=100)
    proficiency: str | None = Field(default=None, max_length=100)


class ContactInfo(StrictModel):
    email: str = ""
    phone: str = ""

    @field_validator("email")
    @classmethod
    def email_may_be_blank(cls, value: str) -> str:
        if value and ("@" not in value or value.startswith("@") or value.endswith("@")):
            raise ValueError("must be a valid email address or blank")
        return value


class Location(StrictModel):
    city: str = ""
    region: str = ""
    country: str = ""


class WorkAuthorization(StrictModel):
    countries: list[str] = Field(default_factory=list)
    notes: str = ""


class WorkPreferences(StrictModel):
    arrangements: list[str] = Field(default_factory=list)
    locations: list[str] = Field(default_factory=list)

    @field_validator("arrangements")
    @classmethod
    def known_arrangements(cls, values: list[str]) -> list[str]:
        allowed = {"remote", "hybrid", "onsite"}
        invalid = [value for value in values if value.lower() not in allowed]
        if invalid:
            raise ValueError(f"unsupported arrangement(s): {', '.join(invalid)}")
        return [value.lower() for value in values]


class WorkHistoryItem(StrictModel):
    employer: str = ""
    title: str = ""
    start_date: str = ""
    end_date: str = ""
    responsibilities: list[str] = Field(default_factory=list)


class Project(StrictModel):
    name: str = ""
    description: str = ""
    technologies: list[str] = Field(default_factory=list)
    results: list[str] = Field(default_factory=list)


class Education(StrictModel):
    institution: str = ""
    qualification: str = ""
    field: str = ""
    start_date: str = ""
    end_date: str = ""


class Certification(StrictModel):
    name: str = ""
    issuer: str = ""
    date: str = ""


class Links(StrictModel):
    portfolio: str = ""
    github: str = ""
    linkedin: str = ""
    other: list[str] = Field(default_factory=list)


class SalaryExpectations(StrictModel):
    minimum: float | None = Field(default=None, ge=0)
    maximum: float | None = Field(default=None, ge=0)
    currency: str = ""

    @field_validator("maximum")
    @classmethod
    def ordered_range(cls, value: float | None, info: Any) -> float | None:
        minimum = info.data.get("minimum")
        if value is not None and minimum is not None and value < minimum:
            raise ValueError("maximum must be greater than or equal to minimum")
        return value


class EmploymentPreferences(StrictModel):
    preferences: list[str] = Field(default_factory=list)
    deal_breakers: list[str] = Field(default_factory=list)


class Profile(StrictModel):
    full_name: str = ""
    contact: ContactInfo = Field(default_factory=ContactInfo)
    location: Location = Field(default_factory=Location)
    work_authorization: WorkAuthorization = Field(default_factory=WorkAuthorization)
    work_preferences: WorkPreferences = Field(default_factory=WorkPreferences)
    target_roles: list[str] = Field(default_factory=list)
    years_experience: float | None = Field(default=None, ge=0, le=100)
    skills: list[ProfileSkill] = Field(default_factory=list)
    work_history: list[WorkHistoryItem] = Field(default_factory=list)
    projects: list[Project] = Field(default_factory=list)
    education: list[Education] = Field(default_factory=list)
    certifications: list[Certification] = Field(default_factory=list)
    links: Links = Field(default_factory=Links)
    salary_expectations: SalaryExpectations = Field(default_factory=SalaryExpectations)
    employment_preferences: EmploymentPreferences = Field(default_factory=EmploymentPreferences)

