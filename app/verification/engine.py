import re
from datetime import timedelta
from urllib.parse import urlsplit

from app.domain.models import VerificationClassification
from app.verification.models import (
    SignalKind,
    VerificationAssessment,
    VerificationEvidence,
    VerificationSignal,
)

POLICY_VERSION = "1.0"
VERIFIED_THRESHOLD = 70
SUSPICIOUS_RISK_THRESHOLD = 40
ATS_HOSTS = {
    "boards.greenhouse.io",
    "job-boards.greenhouse.io",
    "jobs.lever.co",
    "jobs.eu.lever.co",
    "jobs.ashbyhq.com",
}
ATS_SOURCES = {"greenhouse", "lever", "ashby"}

RISK_PATTERNS: tuple[tuple[str, int, str, re.Pattern[str]], ...] = (
    ("upfront_fee_request", 60, "Posting appears to request an upfront application, recruitment, training, or equipment fee", re.compile(r"(?:pay|send|submit)\s+(?:an?\s+)?(?:upfront\s+)?(?:application|recruitment|training|equipment)\s+fee|upfront fee of", re.I)),
    ("bank_credentials_request", 80, "Posting appears to request bank login credentials or an online banking password", re.compile(r"(?:bank|online banking)\s+(?:login|log-in|password|credentials)|credentials\s+for\s+(?:your\s+)?bank", re.I)),
    ("identity_document_request", 35, "Posting appears to request a passport or government ID before an application review", re.compile(r"(?:upload|send|email|provide)\s+(?:your\s+)?(?:passport|government[- ]issued id|national id)", re.I)),
    ("guaranteed_or_no_interview", 40, "Posting appears to promise guaranteed employment or hiring without an interview", re.compile(r"guaranteed\s+(?:employment|job|position)|no interview required|hired without an interview", re.I)),
    ("urgency_pressure", 20, "Posting uses unusually high-pressure hiring language", re.compile(r"apply immediately or lose|hire(?:d|ing)?\s+immediately|instant hiring", re.I)),
)


def assess_evidence(
    evidence: VerificationEvidence,
    *,
    verified_threshold: int = VERIFIED_THRESHOLD,
    suspicious_risk_threshold: int = SUSPICIOUS_RISK_THRESHOLD,
) -> VerificationAssessment:
    """Score observable posting evidence. This does not certify employer trustworthiness."""
    signals: list[VerificationSignal] = []
    missing: list[str] = []
    uncertainty: list[str] = ["A technically verified posting does not guarantee that the employer is trustworthy."]
    original_url = str(evidence.original_url)
    source_url = str(evidence.source_url)
    original_host = (urlsplit(original_url).hostname or "").casefold()
    source_host = (urlsplit(source_url).hostname or "").casefold()

    def positive(name: str, weight: int, finding: str, url: str, source: str, reliability: float) -> None:
        signals.append(VerificationSignal(name=name, kind=SignalKind.POSITIVE, weight=weight, finding=finding, evidence_url=url, data_source=source, reliability=reliability, observed_at=evidence.observed_at))

    if evidence.source_name.casefold() in ATS_SOURCES and source_host in {"boards-api.greenhouse.io", "api.lever.co", "api.eu.lever.co", "api.ashbyhq.com"}:
        positive("published_in_public_ats_feed", 25, "Listing was returned by a recognized ATS public job-board endpoint", source_url, "public ATS endpoint", 0.9)
    else:
        missing.append("No recognized public ATS source evidence is available")

    if evidence.link_accessible is True:
        positive("original_posting_accessible", 25, "Original posting returned a successful response during link checking", original_url, "ATS link check", 0.9)
    elif evidence.link_accessible is False:
        missing.append("The original posting did not pass the supported-host link check")
    else:
        missing.append("No live-link check result is available")

    if original_host in ATS_HOSTS:
        positive("recognized_ats_host", 20, "Original posting is hosted on a recognized ATS domain", original_url, "posting URL host", 0.85)
    else:
        missing.append("Original posting is not on a recognized ATS host")

    if evidence.employer.strip() and evidence.title.strip() and len(evidence.description.strip()) >= 80:
        positive("posting_has_core_details", 10, "Posting includes employer label, title, and a substantive description", original_url, "public posting content", 0.75)
    else:
        missing.append("Employer, title, or substantive job-description detail is missing")

    if evidence.posted_at is not None and evidence.observed_at - timedelta(days=30) <= evidence.posted_at <= evidence.observed_at:
        positive("publication_date_available", 10, "An explicit publication timestamp is available", source_url, "ATS publication field", 0.9)
    else:
        missing.append("A recent explicit publication date was not supplied by the source")
        if evidence.posted_at is not None:
            uncertainty.append("The explicit publication timestamp is outside the 30-day freshness window or is in the future.")
        else:
            uncertainty.append("The source may publish active jobs without exposing their original publication date.")

    missing.append("Independent employer-domain or business-registration evidence was not checked")
    uncertainty.append("The assessment verifies the public posting and its ATS evidence, not the legal identity or conduct of the employer.")

    risk_points = 0
    risk_text = f"{evidence.title}\n{evidence.description}"
    negated_request = re.compile(r"\b(?:we|our company|the employer)\s+(?:do not|don't|never|will not|won't)\s+(?:ask|require|charge|request|collect)\b.{0,80}$", re.I)
    for name, weight, finding, pattern in RISK_PATTERNS:
        match = pattern.search(risk_text)
        if match and not negated_request.search(risk_text[max(0, match.start() - 100):match.start()]):
            risk_points += weight
            signals.append(VerificationSignal(name=name, kind=SignalKind.RISK, weight=weight, finding=finding, evidence_url=evidence.original_url, data_source="deterministic posting-text rule", reliability=0.75, observed_at=evidence.observed_at))

    support = sum(signal.weight for signal in signals if signal.kind == SignalKind.POSITIVE)
    final_score = max(0, min(100, support - min(risk_points, 100)))
    required_evidence = (
        evidence.source_name.casefold() in ATS_SOURCES
        and source_host in {"boards-api.greenhouse.io", "api.lever.co", "api.eu.lever.co", "api.ashbyhq.com"}
        and evidence.link_accessible is True
        and original_host in ATS_HOSTS
    )

    if risk_points >= suspicious_risk_threshold:
        classification = VerificationClassification.SUSPICIOUS
    elif final_score >= verified_threshold and required_evidence:
        classification = VerificationClassification.VERIFIED
    else:
        classification = VerificationClassification.PROBABLE

    reasons = [signal.finding for signal in signals]
    if classification == VerificationClassification.SUSPICIOUS:
        reasons.append(f"Risk signal total ({risk_points}) meets the suspicious threshold ({suspicious_risk_threshold}).")
    elif classification == VerificationClassification.VERIFIED:
        reasons.append("Required ATS feed, accessible original posting, and recognized ATS host evidence are present.")
    else:
        reasons.append("Evidence is incomplete or below the verified threshold; manual review is required.")

    return VerificationAssessment(
        classification=classification,
        score=final_score,
        support_score=support,
        risk_score=risk_points,
        signals=signals,
        reasons=reasons,
        missing_evidence=missing,
        uncertainty=uncertainty,
        policy_version=POLICY_VERSION,
        policy_parameters={"verified_threshold": verified_threshold, "suspicious_risk_threshold": suspicious_risk_threshold},
    )

