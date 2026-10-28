import re

from app.documents.models import ApplicationDocumentContent, ClaimValidationReport, FactClaim
from app.documents.builder import render_cover_letter_text
from app.domain.models import Profile


_PATH_TOKEN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*|\[\d+\]")
_VALID_PATH = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*|\[\d+\])*$")


def resolve_profile_fact(profile: Profile, path: str) -> str | None:
    if not _VALID_PATH.fullmatch(path):
        return None
    tokens = _PATH_TOKEN.findall(path)
    current = profile.model_dump(mode="python")
    try:
        for token in tokens:
            if token.startswith("["):
                current = current[int(token[1:-1])]
            elif isinstance(current, dict):
                current = current[token]
            else:
                return None
    except (KeyError, IndexError, TypeError, ValueError):
        return None
    return current if isinstance(current, str) else None


def _collect_claims(value) -> list[FactClaim]:
    if isinstance(value, FactClaim):
        return [value]
    if isinstance(value, list):
        return [claim for child in value for claim in _collect_claims(child)]
    if hasattr(value, "model_dump"):
        return [claim for child in value.__dict__.values() for claim in _collect_claims(child)]
    return []


def validate_document_claims(
    profile: Profile,
    content: ApplicationDocumentContent,
    cv_text: str,
    *,
    cover_text: str | None = None,
) -> ClaimValidationReport:
    """Reject claims with missing evidence, altered wording, or missing placement in output."""
    claims = _collect_claims(content.cv) + content.cover_letter.selected_profile_facts[:]
    if content.cover_letter.candidate_name:
        claims.append(content.cover_letter.candidate_name)
    unsupported: list[str] = []
    cv_normalized = " ".join(cv_text.split()).casefold()
    letter_source = cover_text if cover_text is not None else render_cover_letter_text(content.cover_letter)
    letter_normalized = " ".join(letter_source.split()).casefold()
    for claim in claims:
        source = resolve_profile_fact(profile, claim.profile_path)
        if source is None:
            unsupported.append(f"{claim.profile_path}: evidence path does not resolve to a profile fact")
        elif " ".join(source.split()).casefold() != " ".join(claim.text.split()).casefold():
            unsupported.append(f"{claim.profile_path}: claim wording differs from the profile fact")
        else:
            target = letter_normalized if claim in content.cover_letter.selected_profile_facts or claim == content.cover_letter.candidate_name else cv_normalized
            if " ".join(claim.text.split()).casefold() not in target:
                unsupported.append(f"{claim.profile_path}: supported fact is absent from the rendered document")
    return ClaimValidationReport(passed=not unsupported, unsupported_claims=unsupported, checked_claim_count=len(claims))

