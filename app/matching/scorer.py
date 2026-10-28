import re

from pydantic import BaseModel, ConfigDict, Field

from app.discovery.models import DiscoveredJob
from app.domain.models import Profile


class MatchResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    score: int = Field(ge=0, le=100)
    explanation: str
    matched_requirements: list[str]
    missing_requirements: list[str]
    disqualifying_constraints: list[str]


def _tokens(value: str) -> set[str]:
    return set(re.findall(r"[a-z0-9+#.]+", value.casefold()))


def score_job(job: DiscoveredJob, profile: Profile) -> MatchResult:
    """Transparent deterministic score using explicit profile fields and job text."""
    description_tokens = _tokens(f"{job.title} {job.description}")
    skills = {skill.name for skill in profile.skills}
    matched_skills = sorted(skill for skill in skills if _tokens(skill) and _tokens(skill) <= description_tokens)
    missing_skills = sorted(skill for skill in skills if skill not in matched_skills)
    skill_score = round(35 * len(matched_skills) / len(skills)) if skills else 0

    roles = profile.target_roles
    role_hits = [role for role in roles if _tokens(role) & _tokens(job.title)]
    role_score = 15 if role_hits else (0 if roles else 8)

    min_experience = re.search(r"(\d+)\+?\s+years?", job.description, re.I)
    required_years = int(min_experience.group(1)) if min_experience else None
    experience_score = 10
    seniority_score = 10
    disqualifiers: list[str] = []
    if required_years is not None:
        if profile.years_experience is None:
            experience_score = 0
            seniority_score = 0
            disqualifiers.append("Years of experience are not supplied in the profile")
        elif profile.years_experience < required_years:
            experience_score = 0
            seniority_score = 0
            disqualifiers.append(f"Role requests {required_years}+ years; profile lists {profile.years_experience:g}")

    arrangements = profile.work_preferences.arrangements
    workplace = job.workplace_type.casefold().replace("_", "-")
    actual_arrangement = next((value for value in ("remote", "hybrid", "on-site") if value in workplace), None)
    accepted_arrangements = {"onsite" if item == "on-site" else item for item in arrangements}
    if actual_arrangement and accepted_arrangements:
        matching_workplace = ("onsite" if actual_arrangement == "on-site" else actual_arrangement) in accepted_arrangements
        location_score = 10 if matching_workplace else 0
        if not matching_workplace:
            disqualifiers.append(f"Work arrangement {job.workplace_type} conflicts with profile preferences")
    elif job.location and profile.work_preferences.locations:
        matching_location = any(target.casefold() in job.location.casefold() for target in profile.work_preferences.locations)
        location_score = 10 if matching_location else 0
        if not matching_location:
            disqualifiers.append("Job location does not match configured location preferences")
    else:
        location_score = 5

    authorization_score = 10
    authorized = profile.work_authorization.countries
    location = job.location.casefold()
    matched_country = next((country for country in authorized if country.casefold() in location), None)
    if authorized and job.location and not matched_country:
        authorization_score = 5  # Location format may omit a country; treat this as uncertain.

    salary_score = 5
    if job.salary_text and profile.salary_expectations.minimum is not None:
        currencies = {"$": "USD", "€": "EUR", "£": "GBP", "₦": "NGN"}
        salary_currency = next((code for symbol, code in currencies.items() if symbol in job.salary_text), "")
        salary_currency = salary_currency or next((code for code in ("USD", "EUR", "GBP", "NGN") if re.search(rf"\b{code}\b", job.salary_text, re.I)), "")
        configured_currency = profile.salary_expectations.currency.upper()
        numbers = [float(match.replace(",", "")) for match in re.findall(r"\d[\d,]*(?:\.\d+)?", job.salary_text)]
        multiplier = 1000 if re.search(r"\d\s*k\b", job.salary_text, re.I) else 1
        maximum_salary = max(numbers, default=0) * multiplier
        if salary_currency and configured_currency and salary_currency != configured_currency:
            salary_score = 0
            disqualifiers.append("Disclosed salary currency differs from profile expectation currency")
        elif salary_currency and configured_currency and salary_currency == configured_currency and maximum_salary and maximum_salary < profile.salary_expectations.minimum:
            salary_score = 0
            disqualifiers.append("Disclosed salary range is below the profile minimum")

    for deal_breaker in profile.employment_preferences.deal_breakers:
        if deal_breaker.strip() and deal_breaker.casefold() in f"{job.title} {job.description} {job.location}".casefold():
            disqualifiers.append(f"Configured deal-breaker appears in the posting: {deal_breaker}")

    total = max(0, min(100, skill_score + role_score + experience_score + seniority_score + location_score + authorization_score + salary_score))
    missing = list(missing_skills)
    if roles and not role_hits:
        missing.append("No configured target role clearly matches the job title")
    explanation = f"Skills {skill_score}/35; target role {role_score}/15; experience {experience_score}/10; seniority {seniority_score}/10; location/work arrangement {location_score}/10; work authorization {authorization_score}/10; salary {salary_score}/10."
    return MatchResult(score=total, explanation=explanation, matched_requirements=matched_skills + role_hits, missing_requirements=missing, disqualifying_constraints=disqualifiers)

