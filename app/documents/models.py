from pydantic import BaseModel, ConfigDict, Field

from app.llm.models import JobDescriptionAnalysis


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class FactClaim(StrictModel):
    profile_path: str
    text: str = Field(min_length=1)


class CVExperience(StrictModel):
    employer: FactClaim | None = None
    title: FactClaim | None = None
    start_date: FactClaim | None = None
    end_date: FactClaim | None = None
    responsibilities: list[FactClaim]


class CVProject(StrictModel):
    name: FactClaim | None = None
    description: FactClaim | None = None
    technologies: list[FactClaim]
    results: list[FactClaim]


class CVEducation(StrictModel):
    institution: FactClaim | None = None
    qualification: FactClaim | None = None
    field: FactClaim | None = None
    start_date: FactClaim | None = None
    end_date: FactClaim | None = None


class CVCertification(StrictModel):
    name: FactClaim | None = None
    issuer: FactClaim | None = None
    date: FactClaim | None = None


class CVDocument(StrictModel):
    full_name: FactClaim | None = None
    contact: list[FactClaim]
    links: list[FactClaim]
    skills: list[FactClaim]
    experience: list[CVExperience]
    projects: list[CVProject]
    education: list[CVEducation]
    certifications: list[CVCertification]


class CoverLetter(StrictModel):
    employer: str
    role: str
    selected_profile_facts: list[FactClaim] = Field(max_length=3)
    candidate_name: FactClaim | None = None


class ClaimValidationReport(StrictModel):
    passed: bool
    unsupported_claims: list[str]
    checked_claim_count: int


class ApplicationDocumentContent(StrictModel):
    cv: CVDocument
    cover_letter: CoverLetter
    job_analysis: JobDescriptionAnalysis
    cover_letter_word_count: int = Field(ge=0, le=249)

