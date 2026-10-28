import hashlib
import json
import re
from urllib.parse import urlsplit
from uuid import UUID

from app.domain.models import Profile
from app.submission.models import FormSnapshot, SubmissionFieldPlan, SubmissionPlan


class SubmissionBlocked(ValueError):
    def __init__(self, reason_code: str, message: str):
        super().__init__(message)
        self.reason_code = reason_code


GREENHOUSE_PAGE_HOSTS = {"boards.greenhouse.io", "job-boards.greenhouse.io"}
GREENHOUSE_ACTION_HOSTS = GREENHOUSE_PAGE_HOSTS | {"boards-api.greenhouse.io"}
LEVER_PAGE_HOSTS = {"jobs.lever.co", "jobs.eu.lever.co"}
LEVER_ACTION_HOSTS = LEVER_PAGE_HOSTS | {"api.lever.co", "api.eu.lever.co"}

CAPTCHA_PATTERNS = (
    re.compile(r"captcha|recaptcha|hcaptcha|turnstile", re.I),
    re.compile(r"verify (that )?you are human|i am not a robot", re.I),
)
LOGIN_PATTERNS = (
    re.compile(r"sign in to apply|log in to continue|login required", re.I),
)


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def adapter_for_url(url: str) -> tuple[str, set[str], set[str]]:
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError as exc:
        raise SubmissionBlocked("UNSUPPORTED_URL", "Submission is limited to valid HTTPS URLs on supported ATS hosts.") from exc
    if parts.scheme != "https" or port not in (None, 443) or parts.username or parts.password:
        raise SubmissionBlocked("UNSUPPORTED_URL", "Submission is limited to HTTPS on supported ATS hosts.")
    host = (parts.hostname or "").casefold()
    if host in GREENHOUSE_PAGE_HOSTS:
        return "greenhouse", GREENHOUSE_PAGE_HOSTS, GREENHOUSE_ACTION_HOSTS
    if host in LEVER_PAGE_HOSTS:
        return "lever", LEVER_PAGE_HOSTS, LEVER_ACTION_HOSTS
    raise SubmissionBlocked("UNSUPPORTED_ATS", "This posting does not use a supported Greenhouse or Lever application page.")


def _field_alias(field) -> str | None:
    key_value = field.key.split(":", maxsplit=1)[-1]
    normalized = [re.sub(r"[^a-z0-9]+", "", item.casefold()) for item in (field.label, key_value)]
    values = set(normalized)
    aliases = {
        "first_name": {"firstname", "first", "givenname"},
        "last_name": {"lastname", "surname", "familyname"},
        "full_name": {"name", "fullname", "candidateName".casefold()},
        "contact.email": {"email", "emailaddress"},
        "contact.phone": {"phone", "phonenumber", "mobile", "telephone"},
        "links.linkedin": {"linkedin", "linkedinurl", "linkedinprofile"},
        "links.github": {"github", "githuburl", "githubprofile"},
        "links.portfolio": {"portfolio", "portfoliourl", "personalwebsite"},
        "cv_pdf_path": {"resume", "cv", "resumeattachment", "curriculumvitae"},
        "cover_letter_pdf_path": {"coverletter", "coverletterattachment"},
    }
    matches = [source for source, source_aliases in aliases.items() if values & source_aliases]
    return matches[0] if len(matches) == 1 else None


def _resolve_profile_value(profile: Profile, path: str) -> str:
    if path == "__profile_first_name":
        parts = profile.full_name.split()
        return parts[0] if len(parts) == 2 else ""
    if path == "__profile_last_name":
        parts = profile.full_name.split()
        return parts[1] if len(parts) == 2 else ""
    if path == "full_name":
        return profile.full_name
    values = {
        "contact.email": profile.contact.email,
        "contact.phone": profile.contact.phone,
        "links.linkedin": profile.links.linkedin,
        "links.github": profile.links.github,
        "links.portfolio": profile.links.portfolio,
    }
    return values.get(path, "")


def resolve_plan_value(profile: Profile, plan_field: SubmissionFieldPlan, artifact_paths: dict[str, str]) -> str:
    if plan_field.profile_path:
        return _resolve_profile_value(profile, plan_field.profile_path)
    if plan_field.document_key:
        return artifact_paths[plan_field.document_key]
    return ""


def _host_allowed(url: str, allowlist: set[str]) -> bool:
    try:
        parts = urlsplit(url)
        return (parts.scheme == "https" and parts.port in (None, 443) and not parts.username and not parts.password
                and (parts.hostname or "").casefold() in allowlist)
    except ValueError:
        return False


def build_submission_plan(
    snapshot: FormSnapshot,
    profile: Profile,
    *,
    application_id: UUID,
    approval_id: UUID,
    document_version: int,
    verification_id: UUID,
    answers_hash: str,
    profile_hash: str,
    job_url: str,
    artifact_paths: dict[str, str],
    artifact_hashes: dict[str, str] | None = None,
) -> SubmissionPlan:
    adapter, page_hosts, action_hosts = adapter_for_url(job_url)
    if not _host_allowed(snapshot.current_url, page_hosts):
        raise SubmissionBlocked("OFFSITE_REDIRECT", "The application page redirected outside the supported ATS host.")
    page_text = snapshot.page_text[:20000]
    if any(pattern.search(page_text) for pattern in CAPTCHA_PATTERNS) or any(
        any(pattern.search(frame_url) for pattern in CAPTCHA_PATTERNS) for frame_url in snapshot.frame_urls
    ):
        raise SubmissionBlocked("CAPTCHA", "A CAPTCHA or bot challenge requires manual intervention.")
    if any(pattern.search(page_text) for pattern in LOGIN_PATTERNS) or any(
        field.control_type == "password" and field.visible for field in snapshot.fields
    ):
        raise SubmissionBlocked("LOGIN_REQUIRED", "This application page requires a login; sign-in automation is disabled.")
    if snapshot.form_count != 1:
        raise SubmissionBlocked("FORM_AMBIGUOUS", "Expected exactly one application form; manual review is required.")
    if snapshot.submit_button_count != 1:
        raise SubmissionBlocked("SUBMIT_CONTROL_AMBIGUOUS", "Expected exactly one submit control; manual review is required.")
    if snapshot.form_method.casefold() != "post":
        raise SubmissionBlocked("UNSAFE_FORM_METHOD", "The form does not use POST; no applicant data was entered.")
    action = snapshot.form_action or snapshot.current_url
    if not _host_allowed(action, action_hosts):
        raise SubmissionBlocked("UNSUPPORTED_FORM_ACTION", "The form submits to an unrecognized host; no applicant data was entered.")

    planned: list[SubmissionFieldPlan] = []
    artifact_hashes = artifact_hashes or {}
    seen_sources: set[str] = set()
    for field in snapshot.fields:
        if not field.visible or field.control_type.casefold() in {"hidden", "submit", "button", "reset", "image"}:
            continue
        if not re.fullmatch(r"(?:id|name):[A-Za-z0-9_.:-]+", field.key):
            raise SubmissionBlocked("UNLOCATABLE_FIELD", "A visible form field has no safe stable identifier; manual application is required.")
        source = _field_alias(field)
        if source is None:
            raise SubmissionBlocked("UNKNOWN_FIELD", f"The application contains an unrecognized field ({field.label or field.key}).")
        if source in seen_sources:
            raise SubmissionBlocked("AMBIGUOUS_FIELD", f"More than one form field maps to the same profile value ({field.label or field.key}).")
        seen_sources.add(source)
        if source.endswith("_pdf_path"):
            if field.control_type.casefold() != "file":
                raise SubmissionBlocked("FIELD_TYPE_MISMATCH", f"The supported attachment field has an unexpected type ({field.label or field.key}).")
            document_key = source
            profile_path = None
            value = artifact_paths.get(source, "")
            source_hash = artifact_hashes.get(source, "")
            if value and not re.fullmatch(r"[a-f0-9]{64}", source_hash):
                raise SubmissionBlocked("DOCUMENT_HASH_MISSING", "The approved attachment has no integrity hash.")
        else:
            if field.control_type.casefold() not in {"text", "email", "tel", "url"}:
                raise SubmissionBlocked("FIELD_TYPE_MISMATCH", f"The supported profile field has an unexpected type ({field.label or field.key}).")
            document_key = None
            profile_path = {"first_name": "__profile_first_name", "last_name": "__profile_last_name"}.get(source, source)
            value = _resolve_profile_value(profile, profile_path)
            source_hash = _sha(value)
        if field.required and not value.strip():
            raise SubmissionBlocked("MISSING_PROFILE_FACT", f"A required field has no verified profile value ({field.label or field.key}).")
        if not value and not field.required:
            continue
        planned.append(SubmissionFieldPlan(
            field_key=field.key,
            label=field.label or field.key,
            control_type=field.control_type,
            required=field.required,
            profile_path=profile_path,
            document_key=document_key,
            source_hash=source_hash,
        ))

    if not planned:
        raise SubmissionBlocked("NO_SUPPORTED_FIELDS", "No supported applicant fields were found on this form.")
    fingerprint_payload = {
        "action": action,
        "method": snapshot.form_method.casefold(),
        "submit_button_count": snapshot.submit_button_count,
        "fields": [field.model_dump(mode="json") for field in snapshot.fields if field.visible],
    }
    fingerprint = _sha(json.dumps(fingerprint_payload, sort_keys=True, separators=(",", ":")))
    partial = SubmissionPlan(
        adapter=adapter,
        application_id=str(application_id),
        approval_id=str(approval_id),
        job_url=job_url,
        form_action=action,
        form_fingerprint=fingerprint,
        profile_hash=profile_hash,
        document_version=document_version,
        verification_id=str(verification_id),
        answers_hash=answers_hash,
        fields=planned,
    )
    plan_hash = _sha(json.dumps(partial.model_dump(mode="json", exclude={"plan_hash"}), sort_keys=True, separators=(",", ":")))
    return partial.model_copy(update={"plan_hash": plan_hash})


def form_fingerprint(snapshot: FormSnapshot) -> str:
    action = snapshot.form_action or snapshot.current_url
    payload = {
        "action": action,
        "method": snapshot.form_method.casefold(),
        "submit_button_count": snapshot.submit_button_count,
        "fields": [field.model_dump(mode="json") for field in snapshot.fields if field.visible],
    }
    return _sha(json.dumps(payload, sort_keys=True, separators=(",", ":")))
