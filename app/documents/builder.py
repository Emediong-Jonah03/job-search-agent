import re

from app.discovery.models import DiscoveredJob
from app.domain.models import Profile
from app.documents.models import (
    ApplicationDocumentContent,
    CVCertification,
    CVDocument,
    CVEducation,
    CVExperience,
    CVProject,
    CoverLetter,
    FactClaim,
)
from app.llm.models import JobDescriptionAnalysis


def _claim(path: str, value: str | None) -> FactClaim | None:
    if value is None or not value.strip():
        return None
    return FactClaim(profile_path=path, text=value.strip())


def _claims(path: str, values: list[str]) -> list[FactClaim]:
    return [FactClaim(profile_path=f"{path}[{index}]", text=value.strip()) for index, value in enumerate(values) if value.strip()]


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9+#.]+", text.casefold()))


def _relevance(text: str, keywords: set[str]) -> int:
    return len(_tokens(text) & keywords)


def build_document_content(profile: Profile, job: DiscoveredJob, analysis: JobDescriptionAnalysis) -> ApplicationDocumentContent:
    """Select and reorder exact profile facts; generated connective text is fixed and local."""
    keywords = _tokens(" ".join(analysis.keywords + analysis.required_skills + analysis.preferred_skills))
    cv = CVDocument(
        full_name=_claim("full_name", profile.full_name),
        contact=[claim for claim in (
            _claim("contact.email", profile.contact.email),
            _claim("contact.phone", profile.contact.phone),
            _claim("location.city", profile.location.city),
            _claim("location.region", profile.location.region),
            _claim("location.country", profile.location.country),
        ) if claim],
        links=[claim for claim in (
            _claim("links.portfolio", profile.links.portfolio),
            _claim("links.github", profile.links.github),
            _claim("links.linkedin", profile.links.linkedin),
        ) if claim] + _claims("links.other", profile.links.other),
        skills=sorted(
            [FactClaim(profile_path=f"skills[{index}].name", text=skill.name) for index, skill in enumerate(profile.skills)],
            key=lambda claim: _relevance(claim.text, keywords),
            reverse=True,
        ),
        experience=[
            CVExperience(
                employer=_claim(f"work_history[{index}].employer", item.employer),
                title=_claim(f"work_history[{index}].title", item.title),
                start_date=_claim(f"work_history[{index}].start_date", item.start_date),
                end_date=_claim(f"work_history[{index}].end_date", item.end_date),
                responsibilities=_claims(f"work_history[{index}].responsibilities", item.responsibilities),
            )
            for index, item in sorted(
                enumerate(profile.work_history),
                key=lambda pair: _relevance(" ".join([pair[1].title, *pair[1].responsibilities]), keywords),
                reverse=True,
            )
        ],
        projects=[
            CVProject(
                name=_claim(f"projects[{index}].name", item.name),
                description=_claim(f"projects[{index}].description", item.description),
                technologies=_claims(f"projects[{index}].technologies", item.technologies),
                results=_claims(f"projects[{index}].results", item.results),
            )
            for index, item in sorted(
                enumerate(profile.projects),
                key=lambda pair: _relevance(" ".join([pair[1].name, pair[1].description, *pair[1].technologies, *pair[1].results]), keywords),
                reverse=True,
            )
        ],
        education=[
            CVEducation(
                institution=_claim(f"education[{index}].institution", item.institution),
                qualification=_claim(f"education[{index}].qualification", item.qualification),
                field=_claim(f"education[{index}].field", item.field),
                start_date=_claim(f"education[{index}].start_date", item.start_date),
                end_date=_claim(f"education[{index}].end_date", item.end_date),
            )
            for index, item in enumerate(profile.education)
        ],
        certifications=[
            CVCertification(
                name=_claim(f"certifications[{index}].name", item.name),
                issuer=_claim(f"certifications[{index}].issuer", item.issuer),
                date=_claim(f"certifications[{index}].date", item.date),
            )
            for index, item in enumerate(profile.certifications)
        ],
    )

    candidates: list[tuple[int, FactClaim]] = []
    for index, item in enumerate(profile.work_history):
        for bullet_index, fact in enumerate(item.responsibilities):
            claim = FactClaim(profile_path=f"work_history[{index}].responsibilities[{bullet_index}]", text=fact)
            candidates.append((_relevance(fact, keywords), claim))
    for index, item in enumerate(profile.projects):
        for field, values in (("description", [item.description]), ("results", item.results)):
            for subindex, fact in enumerate(values):
                if not fact.strip():
                    continue
                path = f"projects[{index}].{field}" if field == "description" else f"projects[{index}].results[{subindex}]"
                candidates.append((_relevance(fact, keywords), FactClaim(profile_path=path, text=fact)))

    candidate_name = _claim("full_name", profile.full_name)
    base_letter_words = 43 + (len(candidate_name.text.split()) if candidate_name else 0)
    chosen: list[FactClaim] = []
    for _, fact in sorted(candidates, key=lambda pair: (pair[0], -len(pair[1].text.split())), reverse=True):
        proposed = chosen + [fact]
        words = base_letter_words + sum(len(item.text.split()) for item in proposed)
        if words < 250:
            chosen.append(fact)
        if len(chosen) == 3:
            break

    cover_letter = CoverLetter(
        employer=job.employer,
        role=job.title,
        selected_profile_facts=chosen,
        candidate_name=candidate_name,
    )
    cover_text = render_cover_letter_text(cover_letter)
    word_count = len(cover_text.split())
    return ApplicationDocumentContent(cv=cv, cover_letter=cover_letter, job_analysis=analysis, cover_letter_word_count=word_count)


def render_cover_letter_text(cover_letter: CoverLetter) -> str:
    facts = "\n".join(f"• {claim.text}" for claim in cover_letter.selected_profile_facts)
    name = cover_letter.candidate_name.text if cover_letter.candidate_name else ""
    parts = [
        "Dear Hiring Team,",
        f"I am applying for the {cover_letter.role} position at {cover_letter.employer}.",
    ]
    if facts:
        parts.append(f"Relevant details from my profile include:\n{facts}")
    parts.append("Thank you for considering my application. I would welcome the opportunity to discuss the role.")
    if name:
        parts.append(f"Sincerely,\n{name}")
    return "\n\n".join(parts)

