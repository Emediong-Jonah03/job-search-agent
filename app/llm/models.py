from pydantic import BaseModel, ConfigDict, Field, field_validator


class JobDescriptionAnalysis(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    required_skills: list[str] = Field(default_factory=list, max_length=50)
    preferred_skills: list[str] = Field(default_factory=list, max_length=50)
    responsibilities: list[str] = Field(default_factory=list, max_length=50)
    qualifications: list[str] = Field(default_factory=list, max_length=50)
    keywords: list[str] = Field(default_factory=list, max_length=80)
    minimum_experience_years: str = ""

    @field_validator("required_skills", "preferred_skills", "responsibilities", "qualifications", "keywords")
    @classmethod
    def bounded_nonempty_entries(cls, values: list[str]) -> list[str]:
        cleaned = [value[:300] for value in values if value.strip()]
        return list(dict.fromkeys(cleaned))


class ProviderResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str
    model: str
    analysis: JobDescriptionAnalysis

