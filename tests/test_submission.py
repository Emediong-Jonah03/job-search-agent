from uuid import uuid4

import pytest

from app.domain.models import Profile
from app.submission.models import FormFieldSnapshot, FormSnapshot
from app.submission.planning import SubmissionBlocked, build_submission_plan, form_fingerprint


def snapshot(*fields, text="", action="https://boards.greenhouse.io/apply", method="post", submits=1):
    return FormSnapshot(
        current_url="https://boards.greenhouse.io/company/jobs/123",
        page_text=text,
        form_count=1,
        submit_button_count=submits,
        form_action=action,
        form_method=method,
        fields=list(fields),
    )


def field(key, label, control="text", required=True, visible=True):
    return FormFieldSnapshot(key=key, label=label, control_type=control, required=required, visible=visible)


def plan(form):
    profile = Profile(full_name="Ada Lovelace", contact={"email": "ada@example.test"})
    return build_submission_plan(
        form, profile, application_id=uuid4(), approval_id=uuid4(), document_version=2,
        verification_id=uuid4(), answers_hash="a" * 64, profile_hash="b" * 64,
        job_url="https://boards.greenhouse.io/company/jobs/123",
        artifact_paths={"cv_pdf_path": "/private/cv.pdf", "cover_letter_pdf_path": "/private/cover.pdf"},
    )


def test_greenhouse_supported_form_plan_has_only_known_profile_fields():
    result = plan(snapshot(field("id:first_name", "First Name"), field("name:email", "Email", "email")))
    assert result.adapter == "greenhouse"
    assert [item.profile_path for item in result.fields] == ["__profile_first_name", "contact.email"]
    assert len(result.plan_hash) == 64


def test_lever_supported_form_and_resume_attachment():
    form = FormSnapshot(current_url="https://jobs.lever.co/acme/123", form_count=1, submit_button_count=1,
        form_action="https://jobs.lever.co/acme/123", form_method="post",
        fields=[field("name:email", "Email", "email"), field("id:resume", "Resume", "file")])
    result = build_submission_plan(form, Profile(contact={"email": "ada@example.test"}), application_id=uuid4(), approval_id=uuid4(), document_version=1,
        verification_id=uuid4(), answers_hash="c"*64, profile_hash="d"*64, job_url="https://jobs.lever.co/acme/123", artifact_paths={"cv_pdf_path":"/private/cv.pdf"}, artifact_hashes={"cv_pdf_path":"e"*64})
    assert result.adapter == "lever"
    assert result.fields[-1].document_key == "cv_pdf_path"


@pytest.mark.parametrize("text,code", [("Please complete the CAPTCHA", "CAPTCHA"), ("Sign in to apply", "LOGIN_REQUIRED")])
def test_challenge_and_login_pages_stop(text, code):
    with pytest.raises(SubmissionBlocked) as error:
        plan(snapshot(field("id:email", "Email", "email"), text=text))
    assert error.value.reason_code == code


def test_unknown_visible_question_stops_even_if_optional():
    with pytest.raises(SubmissionBlocked, match="unrecognized field"):
        plan(snapshot(field("id:email", "Email", "email"), field("id:relocate", "Willing to relocate?", required=False)))


@pytest.mark.parametrize("kwargs,code", [
    ({"action": "https://evil.example/submit"}, "UNSUPPORTED_FORM_ACTION"),
    ({"method": "get"}, "UNSAFE_FORM_METHOD"),
    ({"submits": 2}, "SUBMIT_CONTROL_AMBIGUOUS"),
])
def test_unsafe_form_configuration_stops(kwargs, code):
    with pytest.raises(SubmissionBlocked) as error:
        plan(snapshot(field("id:email", "Email", "email"), **kwargs))
    assert error.value.reason_code == code


def test_missing_required_fact_and_duplicate_source_stop():
    with pytest.raises(SubmissionBlocked) as missing:
        plan(snapshot(field("id:email", "Email", "email")))
    assert missing.value.reason_code == "MISSING_PROFILE_FACT"
    with pytest.raises(SubmissionBlocked) as duplicate:
        plan(snapshot(field("id:email", "Email", "email"), field("id:contact_email", "Email address", "email")))
    assert duplicate.value.reason_code == "AMBIGUOUS_FIELD"


def test_ambiguous_multiword_name_is_not_split_into_first_and_last():
    form = snapshot(field("id:first_name", "First Name"), field("id:last_name", "Last Name"))
    with pytest.raises(SubmissionBlocked) as error:
        build_submission_plan(form, Profile(full_name="Mary Jane Smith"), application_id=uuid4(), approval_id=uuid4(), document_version=1, verification_id=uuid4(), answers_hash="a"*64, profile_hash="b"*64, job_url="https://boards.greenhouse.io/company/jobs/123", artifact_paths={})
    assert error.value.reason_code == "MISSING_PROFILE_FACT"


def test_offsite_redirect_and_unstable_field_key_stop():
    form = snapshot(field("id:email", "Email", "email"))
    form.current_url = "https://evil.example/apply"
    with pytest.raises(SubmissionBlocked) as redirect:
        plan(form)
    assert redirect.value.reason_code == "OFFSITE_REDIRECT"
    with pytest.raises(SubmissionBlocked) as unstable:
        plan(snapshot(field("index:2", "Email", "email")))
    assert unstable.value.reason_code == "UNLOCATABLE_FIELD"


def test_url_with_embedded_credentials_is_rejected():
    with pytest.raises(SubmissionBlocked) as error:
        build_submission_plan(snapshot(field("id:email", "Email", "email")), Profile(contact={"email": "ada@example.test"}), application_id=uuid4(), approval_id=uuid4(), document_version=1, verification_id=uuid4(), answers_hash="a"*64, profile_hash="b"*64, job_url="https://user:secret@boards.greenhouse.io/acme/123", artifact_paths={})
    assert error.value.reason_code == "UNSUPPORTED_URL"


def test_form_fingerprint_changes_with_visible_fields():
    original = snapshot(field("id:email", "Email", "email"))
    changed = snapshot(field("id:email", "Email", "email"), field("id:phone", "Phone", "tel", required=False))
    assert form_fingerprint(original) != form_fingerprint(changed)
