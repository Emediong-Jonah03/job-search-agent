import html
import json
import hashlib
import secrets
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.approval.policy import approval_blockers, screening_answers_hash
from app.approval.service import ApprovalDenied, approve_application, submit_for_approval
from app.config import get_settings
from app.db.models import (
    Application, ApplicationApproval, ApplicationDocument, ApplicationNote, ApplicationTransition,
    AuditEvent, FollowUpReminder, Job, JobVerification, ScreeningQuestion, SubmissionAttempt,
    VerificationSignalRecord,
)
from app.db.session import SessionLocal
from app.documents.models import ApplicationDocumentContent
from app.documents.revisions import DocumentRevisionError, revise_application_document
from app.domain.models import ApplicationState
from app.domain.state_machine import InvalidTransition, transition
from app.profile.loader import ProfileLoadError, load_profile
from app.submission.browser import HostedFormBrowser
from app.submission.planning import SubmissionBlocked, build_submission_plan, resolve_plan_value
from app.submission.models import SubmissionPlan
from app.tracking.follow_ups import schedule_confirmed_submission


router = APIRouter()
LOGIN_FAILURES: dict[str, list[float]] = {}
STATE_NAMES = [item.value for item in ApplicationState]
DOCUMENT_PATHS = {
    "cv-pdf": ("cv_pdf_path", "application/pdf", True),
    "cv-docx": ("cv_docx_path", "application/vnd.openxmlformats-officedocument.wordprocessingml.document", False),
    "cover-letter-pdf": ("cover_letter_pdf_path", "application/pdf", True),
    "cover-letter-docx": ("cover_letter_docx_path", "application/vnd.openxmlformats-officedocument.wordprocessingml.document", False),
}


def get_db():
    with SessionLocal() as session:
        yield session


def _e(value: object) -> str:
    return html.escape(str(value if value is not None else ""), quote=True)


def _csrf(request: Request) -> str:
    token = request.session.get("csrf")
    if not token:
        token = secrets.token_urlsafe(32)
        request.session["csrf"] = token
    return token


def _check_csrf(request: Request, data: dict[str, list[str]]) -> None:
    submitted = data.get("csrf_token", [""])[0]
    expected = request.session.get("csrf", "")
    if not expected or not secrets.compare_digest(submitted, expected):
        raise HTTPException(status_code=403, detail="Invalid or expired form token. Reload and try again.")


async def _form(request: Request) -> dict[str, list[str]]:
    body = await request.body()
    if len(body) > 1_048_576:
        raise HTTPException(status_code=413, detail="Form is too large")
    raw = body.decode("utf-8", errors="replace")
    return parse_qs(raw, keep_blank_values=True, max_num_fields=100)


def _is_authenticated(request: Request) -> bool:
    return request.session.get("authenticated") is True


def _login_redirect() -> RedirectResponse:
    return RedirectResponse("/login", status_code=303)


def _layout(request: Request, title: str, body: str, *, authenticated: bool = True) -> HTMLResponse:
    nav = ""
    if authenticated:
        nav = (
            '<form method="post" action="/logout"><input type="hidden" name="csrf_token" value="'
            + _e(_csrf(request))
            + '"><button class="secondary" type="submit">Sign out</button></form>'
        )
    page = (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f'<title>{_e(title)} · Job Application Agent</title>'
        '<link rel="stylesheet" href="/static/dashboard.css"></head><body>'
        '<header class="topbar"><a class="brand" href="/dashboard">Job Application Agent</a>'
        + nav
        + '</header><main>' + body + '</main></body></html>'
    )
    return HTMLResponse(page, headers={"Cache-Control": "no-store"})


def _require_user(request: Request) -> RedirectResponse | None:
    if not _is_authenticated(request):
        return _login_redirect()
    return None


def _profile():
    settings = get_settings()
    try:
        return load_profile(settings.profile_path)
    except ProfileLoadError as exc:
        raise HTTPException(status_code=503, detail="The configured profile is not valid.") from exc


def _profile_fact_options(profile) -> dict[str, str]:
    options: dict[str, str] = {}

    def walk(value, path: str):
        if isinstance(value, dict):
            for key, item in value.items():
                walk(item, f"{path}.{key}" if path else key)
        elif isinstance(value, list):
            for index, item in enumerate(value):
                walk(item, f"{path}[{index}]")
        elif isinstance(value, str) and value.strip():
            options[path] = value

    walk(profile.model_dump(mode="python"), "")
    return options


def _latest_document(session: Session, app_id: UUID) -> ApplicationDocument | None:
    return session.scalar(
        select(ApplicationDocument)
        .where(ApplicationDocument.application_id == app_id)
        .order_by(ApplicationDocument.version.desc())
        .limit(1)
    )


def _latest_verification(session: Session, job_id: UUID) -> JobVerification | None:
    return session.scalar(
        select(JobVerification)
        .where(JobVerification.job_id == job_id)
        .order_by(JobVerification.assessed_at.desc())
        .limit(1)
    )


def _csrf_input(request: Request) -> str:
    return f'<input type="hidden" name="csrf_token" value="{_e(_csrf(request))}">'


@router.get("/", include_in_schema=False)
def root(request: Request):
    return RedirectResponse("/dashboard" if _is_authenticated(request) else "/login", status_code=303)


@router.get("/login", response_class=HTMLResponse, include_in_schema=False)
def login_page(request: Request):
    if _is_authenticated(request):
        return RedirectResponse("/dashboard", status_code=303)
    if get_settings().dashboard_password is None:
        body = '<section class="card login"><h1>Dashboard setup needed</h1><p>Set <code>DASHBOARD_PASSWORD</code> in your local .env, then restart the API.</p></section>'
        return _layout(request, "Sign in", body, authenticated=False)
    request.session["login_csrf"] = secrets.token_urlsafe(32)
    csrf = request.session["login_csrf"]
    body = (
        '<section class="card login"><p class="muted">PRIVATE WORKSPACE</p><h1>Sign in</h1>'
        '<p class="lede">Review jobs and application documents in your local workspace.</p>'
        '<form method="post" action="/login">'
        f'<input type="hidden" name="csrf_token" value="{_e(csrf)}">'
        '<label for="password">Dashboard password</label><input id="password" name="password" type="password" autocomplete="current-password" required autofocus>'
        '<p><button type="submit">Sign in</button></p></form></section>'
    )
    return _layout(request, "Sign in", body, authenticated=False)


@router.post("/login", include_in_schema=False)
async def login(request: Request):
    settings = get_settings()
    if settings.dashboard_password is None:
        raise HTTPException(status_code=503, detail="Dashboard authentication is not configured.")
    data = await _form(request)
    expected = request.session.get("login_csrf", "")
    supplied = data.get("csrf_token", [""])[0]
    if not expected or not secrets.compare_digest(expected, supplied):
        raise HTTPException(status_code=403, detail="Invalid or expired sign-in form. Reload and try again.")
    remote = request.client.host if request.client else "unknown"
    recent = [stamp for stamp in LOGIN_FAILURES.get(remote, []) if stamp > time.monotonic() - 600]
    LOGIN_FAILURES[remote] = recent
    if len(recent) >= 8:
        raise HTTPException(status_code=429, detail="Too many sign-in attempts. Try again in ten minutes.")
    password = data.get("password", [""])[0]
    if not secrets.compare_digest(password, settings.dashboard_password.get_secret_value()):
        LOGIN_FAILURES[remote] = recent + [time.monotonic()]
        body = '<section class="card login"><h1>Sign in failed</h1><p>The password was not accepted.</p><a class="button" href="/login">Try again</a></section>'
        page = _layout(request, "Sign in failed", body, authenticated=False)
        return HTMLResponse(page.body, status_code=401, headers={"Cache-Control": "no-store"})
    LOGIN_FAILURES.pop(remote, None)
    request.session.clear()
    request.session.update({"authenticated": True, "actor": "dashboard-user", "csrf": secrets.token_urlsafe(32)})
    return RedirectResponse("/dashboard", status_code=303)


@router.post("/logout", include_in_schema=False)
async def logout(request: Request):
    if not _is_authenticated(request):
        return _login_redirect()
    data = await _form(request)
    _check_csrf(request, data)
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


@router.get("/dashboard", response_class=HTMLResponse, include_in_schema=False)
def dashboard(request: Request, state: str = "", session: Session = Depends(get_db)):
    redirect = _require_user(request)
    if redirect:
        return redirect
    if state and state not in STATE_NAMES:
        raise HTTPException(status_code=422, detail="Unknown application state filter")
    query = select(Application, Job).join(Job, Job.id == Application.job_id).order_by(Application.updated_at.desc())
    if state:
        query = query.where(Application.state == state)
    rows = session.execute(query).all()
    body = (
        '<p class="muted">PRIVATE REVIEW WORKSPACE</p><h1>Applications</h1>'
        '<p class="lede">Review job evidence, match fit, and document versions before approving an application.</p>'
        '<p><a href="/follow-ups">View follow-up reminders and drafts</a></p>'
        '<section class="card"><div class="toolbar"><h2>Application queue</h2>'
        '<form method="get" action="/dashboard"><label class="muted" for="state">Filter by state</label> '
        '<select id="state" name="state"><option value="">All states</option>'
    )
    for name in STATE_NAMES:
        selected = " selected" if state == name else ""
        body += f'<option value="{_e(name)}"{selected}>{_e(name.replace("_", " ").title())}</option>'
    body += '</select> <button class="secondary" type="submit">Filter</button></form></div>'
    if not rows:
        body += '<p class="muted">No applications match this filter yet. Job discovery runs from configured boards.</p>'
    else:
        body += '<div class="table-wrap"><table><thead><tr><th>Role</th><th>State</th><th>Match</th><th>Verification</th><th>Documents</th><th></th></tr></thead><tbody>'
        for application, job in rows:
            verification = _latest_verification(session, job.id)
            document = _latest_document(session, application.id)
            verified = verification.classification if verification else "NOT ASSESSED"
            doc_label = f"v{document.version}" if document else "Not generated"
            body += (
                '<tr><td><strong>' + _e(job.title) + '</strong><br><span class="muted">' + _e(job.employer) + '</span></td>'
                '<td><span class="badge">' + _e(application.state.replace("_", " ")) + '</span></td>'
                '<td>' + (_e(f"{application.match_score}/100") if application.match_score is not None else "Not scored") + '</td>'
                '<td><span class="badge ' + ("good" if verified == "VERIFIED" else "warn" if verified == "PROBABLE" else "bad" if verified == "SUSPICIOUS" else "") + '">' + _e(verified) + '</span></td>'
                '<td>' + _e(doc_label) + '</td><td><a href="/applications/' + _e(application.id) + '">Review</a></td></tr>'
            )
        body += '</tbody></table></div>'
    body += '</section><p class="footer-note">Approval and submission confirmation are per application. Uncertain outcomes require manual reconciliation.</p>'
    return _layout(request, "Applications", body)


@router.get("/follow-ups", response_class=HTMLResponse, include_in_schema=False)
def follow_ups_page(request: Request, session: Session = Depends(get_db)):
    redirect = _require_user(request)
    if redirect:
        return redirect
    rows = session.execute(
        select(FollowUpReminder, Application, Job)
        .join(Application, Application.id == FollowUpReminder.application_id)
        .join(Job, Job.id == Application.job_id)
        .order_by(FollowUpReminder.scheduled_for.desc())
    ).all()
    body = '<p><a href="/dashboard">← Applications</a></p><h1>Follow-up reminders</h1>'
    body += '<p class="lede">Reminders are created after confirmed submissions. Drafts require your review and are never sent by this dashboard.</p>'
    if rows:
        body += '<section class="card"><div class="table-wrap"><table><thead><tr><th>Role</th><th>State</th><th>Follow-up date (UTC)</th><th>Status</th><th></th></tr></thead><tbody>'
        for reminder, application, job in rows:
            body += (
                f'<tr><td><strong>{_e(job.title)}</strong><br>{_e(job.employer)}</td>'
                f'<td>{_e(application.state.replace("_", " "))}</td><td>{_e(reminder.scheduled_for)}</td>'
                f'<td>{_e(reminder.status.replace("_", " "))}</td>'
                f'<td><a href="/applications/{application.id}">Open application</a></td></tr>'
            )
        body += '</tbody></table></div></section>'
    else:
        body += '<section class="card"><p>No follow-up reminders yet. The system creates them after a confirmed submission.</p></section>'
    return _layout(request, "Follow-up reminders", body)


@router.get("/applications/{application_id}", response_class=HTMLResponse, include_in_schema=False)
def application_detail(application_id: UUID, request: Request, session: Session = Depends(get_db)):
    redirect = _require_user(request)
    if redirect:
        return redirect
    application = session.get(Application, application_id)
    if application is None:
        raise HTTPException(status_code=404, detail="Application not found")
    job = session.get(Job, application.job_id)
    verification = _latest_verification(session, job.id)
    document = _latest_document(session, application.id)
    documents = list(session.scalars(select(ApplicationDocument).where(
        ApplicationDocument.application_id == application.id
    ).order_by(ApplicationDocument.version.desc())))
    active_approval = session.scalar(select(ApplicationApproval).where(
        ApplicationApproval.application_id == application.id,
        ApplicationApproval.invalidated_at.is_(None),
    ).order_by(ApplicationApproval.approved_at.desc()).limit(1))
    submission_attempts = list(session.scalars(select(SubmissionAttempt).where(
        SubmissionAttempt.application_id == application.id
    ).order_by(SubmissionAttempt.started_at.desc())))
    reminder = session.scalar(select(FollowUpReminder).where(FollowUpReminder.application_id == application.id))
    notes = list(session.scalars(select(ApplicationNote).where(
        ApplicationNote.application_id == application.id
    ).order_by(ApplicationNote.created_at.desc()).limit(100)))
    questions = list(session.scalars(select(ScreeningQuestion).where(
        ScreeningQuestion.application_id == application.id
    ).order_by(ScreeningQuestion.id)))
    transitions = list(session.scalars(select(ApplicationTransition).where(
        ApplicationTransition.application_id == application.id
    ).order_by(ApplicationTransition.occurred_at.desc()).limit(30)))
    profile = _profile()
    profile_facts = _profile_fact_options(profile)
    answer_payload = [
        {"question_id": str(question.id), "status": question.status, "answer": question.answer,
         "profile_path": question.answer_profile_path}
        for question in questions
    ]
    answers_hash = screening_answers_hash(answer_payload)
    review_blockers = approval_blockers(
        document.claim_validation if document else None,
        [question.status for question in questions],
        verification.classification if verification else None,
    )
    if any(question.status == "RESOLVED" and (not question.answer or not question.answer_profile_path) for question in questions):
        review_blockers.append("A resolved screening answer is missing its profile fact.")
    csrf = _csrf_input(request)
    body = (
        '<p><a href="/dashboard">← Applications</a></p><p class="muted">APPLICATION REVIEW</p>'
        f'<h1>{_e(job.title)}</h1><p class="lede">{_e(job.employer)} · <span class="badge">{_e(application.state.replace("_", " "))}</span></p>'
        '<div class="grid"><section class="card"><h2>Job details</h2><dl class="fact-list">'
        f'<dt>Match score</dt><dd>{_e(str(application.match_score) + "/100" if application.match_score is not None else "Not scored")}</dd>'
        f'<dt>Original posting</dt><dd><a href="{_e(job.canonical_url)}" target="_blank" rel="noopener noreferrer">Open job listing</a></dd>'
        f'<dt>Posted</dt><dd>{_e(job.posted_at or "Unknown")}</dd>'
        f'<dt>Description</dt><dd>{_e(job.description or "No description saved")}</dd></dl></section>'
    )
    match = application.match_details or {}
    body += '<section class="card"><h2>Match assessment</h2>'
    body += f'<p>{_e(match.get("explanation", "No stored explanation is available for this application."))}</p>'
    if match.get("matched_requirements"):
        body += '<p><strong>Matched:</strong> ' + _e(", ".join(match["matched_requirements"])) + '</p>'
    if match.get("missing_requirements"):
        body += '<p><strong>Skill gaps / missing information:</strong> ' + _e(", ".join(match["missing_requirements"])) + '</p>'
    if match.get("disqualifying_constraints"):
        body += '<p class="callout error"><strong>Potential conflicts:</strong> ' + _e("; ".join(match["disqualifying_constraints"])) + '</p>'
    body += '</section>'
    if verification:
        body += '<section class="card"><h2>Posting evidence</h2>'
        body += f'<p><span class="badge {"good" if verification.classification == "VERIFIED" else "warn" if verification.classification == "PROBABLE" else "bad"}">{_e(verification.classification)}</span> · evidence score {_e(verification.score)}/100</p>'
        body += f'<p>Support {_e(verification.support_score)} · risk {_e(verification.risk_score)} · assessed {_e(verification.assessed_at)}</p>'
        for label, items in (("Findings", verification.reasons), ("Missing evidence", verification.missing_evidence), ("Uncertainty", verification.uncertainty)):
            body += f'<h3>{label}</h3>'
            body += '<ul>' + ''.join(f'<li>{_e(item)}</li>' for item in items) + '</ul>' if items else '<p class="muted">None recorded.</p>'
        signals = list(session.scalars(select(VerificationSignalRecord).where(
            VerificationSignalRecord.verification_id == verification.id
        ).order_by(VerificationSignalRecord.name)))
        body += '<h3>Signals</h3>'
        if signals:
            body += '<ul>'
            for signal in signals:
                evidence = f' · <a href="{_e(signal.evidence_url)}" target="_blank" rel="noopener noreferrer">source</a>' if signal.evidence_url else ''
                body += f'<li><strong>{_e(signal.name)}</strong>: {_e(signal.finding)} ({_e(signal.kind.lower())}, weight {_e(signal.weight)}){evidence}</li>'
            body += '</ul>'
        else:
            body += '<p class="muted">No individual verification signals were stored.</p>'
        body += '<p class="footer-note">A verified posting means the listing is supported by ATS evidence; it is not a guarantee that the employer is trustworthy.</p></section>'
    else:
        body += '<section class="card"><h2>Posting evidence</h2><p class="callout">No verification assessment is stored. This job is not ready for approval.</p></section>'
    body += '</div>'
    body += '<section class="card"><h2>Documents</h2>'
    if document:
        document_content = document.content_json or {}
        body += f'<p>Version {document.version} · claim check: <span class="badge {"good" if document.claim_validation.get("passed") else "bad"}">{"Passed" if document.claim_validation.get("passed") else "Failed"}</span> · {_e(document.claim_validation.get("checked_claim_count", 0))} claims checked</p>'
        body += '<div class="doc-links">'
        for slug, label in (("cv-pdf", "CV PDF"), ("cv-docx", "Download CV DOCX"), ("cover-letter-pdf", "Cover letter PDF"), ("cover-letter-docx", "Download cover letter DOCX")):
            body += f'<a class="button secondary" href="/applications/{application.id}/documents/{slug}">{label}</a>'
        body += '</div>'
        body += f'<iframe class="doc-frame" title="CV PDF preview" src="/applications/{application.id}/documents/cv-pdf?inline=1"></iframe>'
        body += f'<h3>Cover letter preview</h3><iframe class="doc-frame" title="Cover letter PDF preview" src="/applications/{application.id}/documents/cover-letter-pdf?inline=1"></iframe>'
        body += '<h3>Document versions</h3><ul>'
        for versioned in documents:
            body += f'<li>Version {versioned.version} · created {_e(versioned.created_at)} · '
            body += f'<a href="/applications/{application.id}/documents/cv-pdf?version={versioned.version}">CV PDF</a> · '
            body += f'<a href="/applications/{application.id}/documents/cover-letter-pdf?version={versioned.version}">Cover letter PDF</a></li>'
        body += '</ul>'
        if application.state not in {ApplicationState.SUBMITTED.value, ApplicationState.FOLLOW_UP.value, ApplicationState.REJECTED_BY_VERIFICATION.value, ApplicationState.SKIPPED.value}:
            raw_json = json.dumps(document_content, ensure_ascii=False, indent=2)
            body += '<details><summary>Edit structured document source</summary><p class="muted">Edits create a new immutable version. Personal claims must remain exact facts from the profile; unsupported edits are rejected. Editing invalidates any previous approval.</p>'
            body += f'<form method="post" action="/applications/{application.id}/documents/{document.id}/revise">{csrf}<label for="content_json">Document source JSON</label><textarea id="content_json" name="content_json" spellcheck="false">{_e(raw_json)}</textarea><button type="submit">Validate and save new version</button></form></details>'
    else:
        body += '<p class="muted">No generated documents are available for this application.</p>'
    body += '</section>'
    body += '<section class="card"><h2>Screening questions</h2>'
    if questions:
        for question in questions:
            body += f'<div class="callout {"success" if question.status == "RESOLVED" else ""}"><strong>{_e(question.question)}</strong><p>Status: {_e(question.status.replace("_", " "))}</p>'
            if question.status != "RESOLVED":
                body += f'<form method="post" action="/applications/{application.id}/screening/{question.id}/review">{csrf}'
                body += f'<label for="fact-{question.id}">Answer using a verified profile fact</label><select id="fact-{question.id}" name="profile_path"><option value="">Choose a profile fact</option>'
                for path, text in profile_facts.items():
                    selected = " selected" if path == question.answer_profile_path else ""
                    body += f'<option value="{_e(path)}"{selected}>{_e(path)} — {_e(text[:180])}</option>'
                body += '</select><p class="muted">If no profile fact answers this question, mark it as needing clarification. It will block approval until resolved.</p>'
                body += f'<label><input type="checkbox" name="needs_clarification" value="yes"> The profile does not provide an answer</label><p><button type="submit">Save review</button></p></form>'
            elif question.answer:
                body += f'<p>{_e(question.answer)}</p>'
            body += '</div>'
    else:
        body += '<p class="muted">No screening questions have been collected for this application. If a supported application workflow later discovers questions, each will need review before approval.</p>'
    body += '</section>'
    disclosure = []
    for label, value in (
        ("Email", profile.contact.email), ("Phone", profile.contact.phone),
        ("City", profile.location.city), ("Region", profile.location.region),
        ("Country", profile.location.country), ("Portfolio link", profile.links.portfolio),
        ("GitHub link", profile.links.github), ("LinkedIn link", profile.links.linkedin),
    ):
        if value:
            disclosure.append((label, value))
    body += '<section class="card"><h2>Personal-data disclosure</h2><p class="muted">Information from your profile included in the current CV:</p>'
    if disclosure:
        body += '<dl class="fact-list">' + ''.join(f'<dt>{_e(label)}</dt><dd>{_e(value)}</dd>' for label, value in disclosure) + '</dl>'
    else:
        body += '<p>No contact or location details are included.</p>'
    salary = profile.salary_expectations
    salary_range = "Not provided"
    if salary.minimum is not None or salary.maximum is not None:
        salary_range = f'{salary.minimum if salary.minimum is not None else "open"}–{salary.maximum if salary.maximum is not None else "open"} {salary.currency}'.strip()
    body += f'<h3>Proposed application answers from profile</h3><dl class="fact-list"><dt>Salary expectation</dt><dd>{_e(salary_range)}</dd>'
    body += f'<dt>Work authorization</dt><dd>{_e(", ".join(profile.work_authorization.countries) or "Not provided")}</dd>'
    body += f'<dt>Authorization notes</dt><dd>{_e(profile.work_authorization.notes or "None provided")}</dd></dl>'
    body += '<p class="muted">No other application-form field answers have been collected. Unsupported answers will need clarification rather than inference.</p>'
    body += f'<p class="muted">The generated documents also include {_e(len(profile.work_history))} work-history entries, {_e(len(profile.projects))} projects, {_e(len(profile.education))} education entries, and {_e(len(profile.certifications))} certifications from your profile.</p></section>'
    body += '<section class="card"><h2>Review decision</h2>'
    if review_blockers:
        body += '<div class="callout error"><strong>Approval is blocked:</strong><ul>' + ''.join(f'<li>{_e(item)}</li>' for item in review_blockers) + '</ul></div>'
    if application.state == ApplicationState.CV_GENERATED.value:
        if not review_blockers:
            body += f'<form method="post" action="/applications/{application.id}/request-approval">{csrf}<button type="submit">Submit this application for review</button></form>'
    elif application.state == ApplicationState.AWAITING_APPROVAL.value:
        body += '<p class="callout">You are reviewing this specific application. Approval only authorizes the next workflow phase; it does not submit anything.</p>'
        if not review_blockers and document and verification:
            body += f'<form method="post" action="/applications/{application.id}/approve">{csrf}'
            body += f'<input type="hidden" name="document_version" value="{document.version}"><input type="hidden" name="verification_id" value="{_e(verification.id)}"><input type="hidden" name="answers_hash" value="{_e(answers_hash)}">'
            body += '<label><input type="checkbox" name="confirm" value="yes" required> I reviewed this job, evidence, documents, disclosure, and screening answers and approve this application.</label><p><button type="submit">Approve this application</button></p></form>'
    elif application.state == ApplicationState.APPROVED.value:
        approved_version = active_approval.document_version if active_approval else "Unavailable"
        approval_info = f' by {_e(active_approval.approved_by)} at {_e(active_approval.approved_at)}' if active_approval else ''
        body += f'<p class="callout success">Approved{approval_info} for submission with document version {_e(approved_version)}. Approval alone does not submit anything.</p>'
        active_attempt = next((item for item in submission_attempts if item.status in {"PREPARED", "IN_PROGRESS", "OUTCOME_UNKNOWN"}), None)
        if active_attempt and active_attempt.status == "PREPARED":
            saved_plan = SubmissionPlan.model_validate(active_attempt.result_metadata["plan"])
            doc = session.scalar(select(ApplicationDocument).where(ApplicationDocument.application_id == application.id, ApplicationDocument.version == saved_plan.document_version))
            body += '<div class="callout"><strong>Review the exact application-form data</strong><ul>'
            for item in saved_plan.fields:
                value = resolve_plan_value(profile, item, {"cv_pdf_path": doc.cv_pdf_path, "cover_letter_pdf_path": doc.cover_letter_pdf_path} if doc else {})
                display = "CV PDF version " + str(saved_plan.document_version) if item.document_key == "cv_pdf_path" else "Cover letter PDF version " + str(saved_plan.document_version) if item.document_key == "cover_letter_pdf_path" else value
                body += f'<li><strong>{_e(item.label)}:</strong> {_e(display)}</li>'
            body += '</ul></div>'
            body += f'<form method="post" action="/applications/{application.id}/submit">{csrf}<input type="hidden" name="attempt_id" value="{_e(active_attempt.id)}"><label><input type="checkbox" name="confirm" value="yes" required> I reviewed these exact fields and documents and want to submit this application now.</label><p><button class="danger" type="submit">Submit application now</button></p></form>'
        elif not active_attempt and active_approval and document and document.version == active_approval.document_version and verification:
            body += f'<form method="post" action="/applications/{application.id}/prepare-submission">{csrf}<button type="submit">Inspect supported application form</button></form><p class="muted">This opens the hosted form without entering personal data. Unknown fields, login, CAPTCHA, and changed forms stop the workflow.</p>'
        elif active_attempt and active_attempt.status in {"IN_PROGRESS", "OUTCOME_UNKNOWN"}:
            body += '<p class="callout error"><strong>Submission outcome is uncertain. Do not submit again.</strong> Check the employer site directly and reconcile the attempt manually.</p>'
            body += f'<form method="post" action="/applications/{application.id}/reconcile-submission">{csrf}<input type="hidden" name="attempt_id" value="{_e(active_attempt.id)}"><label for="resolution">After checking the employer site, select the confirmed result</label><select id="resolution" name="resolution" required><option value="">Choose a result</option><option value="submitted">Definitely submitted</option><option value="not_submitted">Definitely not submitted</option></select><label for="evidence_ref">Confirmation number or reconciliation note</label><input id="evidence_ref" name="evidence_ref" maxlength="500" required><label><input type="checkbox" name="reconcile_confirm" value="yes" required> I checked the employer site and confirm this result.</label><button type="submit">Record reconciliation</button></form>'
    else:
        body += f'<p class="muted">Current state: {_e(application.state.replace("_", " "))}</p>'
    terminal = {ApplicationState.SUBMITTED.value, ApplicationState.FOLLOW_UP.value, ApplicationState.SKIPPED.value, ApplicationState.REJECTED_BY_VERIFICATION.value}
    if application.state not in terminal and not application.submission_lock:
        body += f'<form method="post" action="/applications/{application.id}/skip" class="actions">{csrf}<button class="danger" type="submit">Skip this application</button></form>'
    body += '</section>'
    body += '<section class="card"><h2>Submission and follow-up</h2>'
    if submission_attempts:
        body += '<ul>'
        for attempt in submission_attempts:
            message = {
                "PREPARED": "Form inspected — awaiting your explicit submit confirmation",
                "CONFIRMED_SUCCESS": "Confirmed successful",
                "CONFIRMED_FAILURE": "Confirmed failed",
                "OUTCOME_UNKNOWN": "Outcome unknown — reconcile manually; do not retry blindly",
                "IN_PROGRESS": "In progress",
                "NOT_ATTEMPTED": "Not attempted",
            }.get(attempt.status, attempt.status)
            body += f'<li><strong>{_e(message)}</strong> · started {_e(attempt.started_at or "not started")} · finished {_e(attempt.finished_at or "pending")}'
            metadata = attempt.result_metadata or {}
            for key in ("confirmation_number", "confirmation_text"):
                if metadata.get(key):
                    body += f'<br>{_e(key.replace("_", " ").title())}: {_e(metadata[key])}'
            if metadata.get("reason"):
                body += f'<br>{_e(metadata["reason"])}'
            if metadata.get("screenshot_path"):
                body += f'<br><a href="/applications/{application.id}/submission-evidence/{attempt.id}">View private submission screenshot</a>'
            body += '</li>'
        body += '</ul>'
    else:
        body += '<p class="muted">No submission attempts are recorded. Approved does not mean submitted.</p>'
    if reminder:
        body += f'<h3>Follow-up reminder</h3><p><span class="badge">{_e(reminder.status.replace("_", " "))}</span> · scheduled {_e(reminder.scheduled_for)} UTC</p>'
        if reminder.draft_text and reminder.status == "DRAFTED":
            body += f'<form method="post" action="/applications/{application.id}/follow-ups/{reminder.id}/save-draft">{csrf}<label for="draft">Follow-up message draft</label><textarea id="draft" name="draft_text" maxlength="5000" required>{_e(reminder.draft_text)}</textarea><button class="secondary" type="submit">Save draft edits</button></form>'
            body += f'<form method="post" action="/applications/{application.id}/follow-ups/{reminder.id}/approve">{csrf}<label for="approved-draft">Message to approve</label><textarea id="approved-draft" name="draft_text" maxlength="5000" required>{_e(reminder.draft_text)}</textarea><label><input type="checkbox" name="confirm" value="yes" required> I reviewed and approve this message for manual use.</label><p><button type="submit">Approve follow-up draft</button></p></form>'
        elif reminder.status == "APPROVED":
            body += f'<div class="callout">{_e(reminder.draft_text or "")}</div><p class="muted">The draft is approved for you to send manually. This dashboard does not send messages.</p>'
            body += f'<form method="post" action="/applications/{application.id}/follow-ups/{reminder.id}/complete">{csrf}<label for="completion-note">Optional note</label><input id="completion-note" name="completion_note" maxlength="1000"><label><input type="checkbox" name="confirm" value="yes" required> I sent or handled this follow-up outside the dashboard.</label><p><button type="submit">Mark follow-up handled</button></p></form>'
        elif reminder.status == "COMPLETED":
            body += f'<p>Handled {_e(reminder.completed_at)} · {_e(reminder.completion_note or "No note")}</p>'
        elif reminder.status == "SCHEDULED":
            body += '<p class="muted">A draft will appear here when the reminder is due. The worker checks due reminders daily.</p>'
    else:
        body += '<p class="muted">A reminder is created only after a submission is confirmed.</p>'
    body += '</section>'
    body += '<section class="card"><h2>Application notes</h2>'
    if notes:
        body += '<ul>' + ''.join(f'<li>{_e(note.created_at)} · {_e(note.body)}</li>' for note in notes) + '</ul>'
    else:
        body += '<p class="muted">No notes have been added.</p>'
    body += f'<form method="post" action="/applications/{application.id}/notes">{csrf}<label for="note">Add a private note</label><textarea id="note" name="body" maxlength="5000" required></textarea><button type="submit">Save note</button></form></section>'
    body += '<section class="card"><h2>State history</h2>'
    if transitions:
        body += '<ol>'
        for item in transitions:
            body += f'<li>{_e(item.occurred_at)} · {_e(item.previous_state or "Created")} → {_e(item.new_state)} · {_e(item.actor)} · {_e(item.reason or "")}</li>'
        body += '</ol>'
    else:
        body += '<p class="muted">No transitions have been recorded.</p>'
    body += '</section>'
    return _layout(request, f"Review {job.title}", body)


@router.get("/applications/{application_id}/documents/{slug}", include_in_schema=False)
def document_file(application_id: UUID, slug: str, request: Request, version: int | None = None, session: Session = Depends(get_db)):
    redirect = _require_user(request)
    if redirect:
        return redirect
    if slug not in DOCUMENT_PATHS:
        raise HTTPException(status_code=404, detail="Document not found")
    if version is None:
        document = _latest_document(session, application_id)
    else:
        document = session.scalar(select(ApplicationDocument).where(
            ApplicationDocument.application_id == application_id,
            ApplicationDocument.version == version,
        ))
    if document is None:
        raise HTTPException(status_code=404, detail="Document not found")
    attribute, media_type, is_pdf = DOCUMENT_PATHS[slug]
    root = Path(get_settings().artifacts_path).expanduser().resolve()
    path = (root / getattr(document, attribute)).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise HTTPException(status_code=404, detail="Stored document not found")
    inline = request.query_params.get("inline") == "1" and is_pdf
    disposition = "inline" if inline else "attachment"
    return FileResponse(path, media_type=media_type, filename=path.name, content_disposition_type=disposition, headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})


@router.post("/applications/{application_id}/documents/{document_id}/revise", include_in_schema=False)
async def revise_document(application_id: UUID, document_id: UUID, request: Request, session: Session = Depends(get_db)):
    if not _is_authenticated(request):
        return _login_redirect()
    data = await _form(request)
    _check_csrf(request, data)
    try:
        content = ApplicationDocumentContent.model_validate_json(data.get("content_json", [""])[0])
        revised = revise_application_document(session, document_id, content, _profile(), expected_application_id=application_id)
    except (ValueError, DocumentRevisionError) as exc:
        message = _e(str(exc))
        response = _layout(request, "Edit rejected", f'<h1>Edits were not saved</h1><p class="callout error">{message}</p><p><a href="/applications/{application_id}">Return to review</a></p>')
        return HTMLResponse(response.body, status_code=422, headers={"Cache-Control": "no-store"})
    return RedirectResponse(f"/applications/{application_id}", status_code=303)


@router.post("/applications/{application_id}/screening/{question_id}/review", include_in_schema=False)
async def review_screening(application_id: UUID, question_id: UUID, request: Request, session: Session = Depends(get_db)):
    if not _is_authenticated(request):
        return _login_redirect()
    data = await _form(request)
    _check_csrf(request, data)
    application = session.scalar(select(Application).where(
        Application.id == application_id
    ).with_for_update())
    if application is None:
        raise HTTPException(status_code=404, detail="Application not found")
    if application.state not in {ApplicationState.CV_GENERATED.value, ApplicationState.AWAITING_APPROVAL.value}:
        raise HTTPException(status_code=409, detail="Screening answers cannot be changed after approval or submission")
    path = data.get("profile_path", [""])[0]
    needs_clarification = data.get("needs_clarification") == ["yes"]
    fact_options = _profile_fact_options(_profile())
    if needs_clarification:
        answer = ""
        path = None
        status = "NEEDS_CLARIFICATION"
    elif path in fact_options:
        answer = fact_options[path]
        status = "RESOLVED"
    else:
        raise HTTPException(status_code=422, detail="Choose an exact profile fact or mark the question as needing clarification")
    question = session.scalar(select(ScreeningQuestion).where(
        ScreeningQuestion.id == question_id,
        ScreeningQuestion.application_id == application_id,
    ).with_for_update())
    if question is None:
        raise HTTPException(status_code=404, detail="Screening question not found")
    question.answer = answer
    question.answer_profile_path = path
    question.status = status
    question.reviewed_by = "dashboard-user"
    question.reviewed_at = datetime.now(timezone.utc)
    session.add(AuditEvent(
        event_type="application.screening_question.reviewed",
        actor="dashboard-user",
        application_id=application_id,
        payload={"question_id": str(question_id), "status": status},
    ))
    session.commit()
    return RedirectResponse(f"/applications/{application_id}", status_code=303)


@router.post("/applications/{application_id}/request-approval", include_in_schema=False)
async def request_approval(application_id: UUID, request: Request, session: Session = Depends(get_db)):
    if not _is_authenticated(request):
        return _login_redirect()
    _check_csrf(request, await _form(request))
    try:
        submit_for_approval(session, application_id, "dashboard-user")
    except ApprovalDenied as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return RedirectResponse(f"/applications/{application_id}", status_code=303)


@router.post("/applications/{application_id}/approve", include_in_schema=False)
async def approve(application_id: UUID, request: Request, session: Session = Depends(get_db)):
    if not _is_authenticated(request):
        return _login_redirect()
    data = await _form(request)
    _check_csrf(request, data)
    if data.get("confirm") != ["yes"]:
        raise HTTPException(status_code=422, detail="Explicit per-application approval is required")
    try:
        expected_version = int(data.get("document_version", [""])[0])
        expected_verification_id = UUID(data.get("verification_id", [""])[0])
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="The review snapshot is invalid. Reload the application.") from exc
    try:
        approve_application(
            session, application_id, "dashboard-user",
            expected_document_version=expected_version,
            expected_verification_id=expected_verification_id,
            expected_answers_hash=data.get("answers_hash", [""])[0],
        )
    except ApprovalDenied as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return RedirectResponse(f"/applications/{application_id}", status_code=303)


@router.post("/applications/{application_id}/skip", include_in_schema=False)
async def skip(application_id: UUID, request: Request, session: Session = Depends(get_db)):
    if not _is_authenticated(request):
        return _login_redirect()
    _check_csrf(request, await _form(request))
    with session.begin():
        application = session.scalar(select(Application).where(Application.id == application_id).with_for_update())
        if application is None:
            raise HTTPException(status_code=404, detail="Application not found")
        if application.submission_lock:
            raise HTTPException(status_code=409, detail="Submission outcome must be reconciled before changing this application")
        active_approval = session.scalar(select(ApplicationApproval).where(
            ApplicationApproval.application_id == application_id,
            ApplicationApproval.invalidated_at.is_(None),
        ).with_for_update())
        if active_approval:
            active_approval.invalidated_at = datetime.now(timezone.utc)
            active_approval.invalidation_reason = "Application was skipped"
        try:
            change = transition(ApplicationState(application.state), ApplicationState.SKIPPED, "dashboard-user", "Skipped from review dashboard")
        except InvalidTransition as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        application.state = change.new.value
        application.version += 1
        session.add(ApplicationTransition(
            application_id=application.id,
            previous_state=change.previous.value,
            new_state=change.new.value,
            actor="dashboard-user",
            reason=change.reason,
            metadata_json={},
        ))
        session.add(AuditEvent(
            event_type="application.state_transition",
            actor="dashboard-user",
            application_id=application.id,
            payload={"previous_state": change.previous.value, "new_state": change.new.value, "reason": change.reason},
        ))
    return RedirectResponse("/dashboard", status_code=303)


@router.post("/applications/{application_id}/prepare-submission", include_in_schema=False)
async def prepare_submission(application_id: UUID, request: Request, session: Session = Depends(get_db)):
    if not _is_authenticated(request):
        return _login_redirect()
    _check_csrf(request, await _form(request))
    application = session.get(Application, application_id)
    if application is None:
        raise HTTPException(status_code=404, detail="Application not found")
    if application.state != ApplicationState.APPROVED.value or application.submission_lock:
        raise HTTPException(status_code=409, detail="Application is not eligible for form preparation")
    approval = session.scalar(select(ApplicationApproval).where(ApplicationApproval.application_id == application_id, ApplicationApproval.invalidated_at.is_(None)).order_by(ApplicationApproval.approved_at.desc()).limit(1))
    document = _latest_document(session, application_id)
    job = session.get(Job, application.job_id)
    verification = _latest_verification(session, job.id)
    questions = list(session.scalars(select(ScreeningQuestion).where(ScreeningQuestion.application_id == application_id).order_by(ScreeningQuestion.id)))
    answers_hash = screening_answers_hash([{"question_id": str(q.id), "status": q.status, "answer": q.answer, "profile_path": q.answer_profile_path} for q in questions])
    profile = _profile()
    profile_hash = hashlib.sha256(json.dumps(profile.model_dump(mode="json"), sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    if not approval or not document or not verification or document.version != approval.document_version or str(verification.id) != str(approval.verification_id) or verification.classification != "VERIFIED" or answers_hash != approval.answers_hash or profile_hash != document.profile_hash:
        raise HTTPException(status_code=409, detail="Approval facts changed or are incomplete. Re-review this application before continuing.")
    try:
        snapshot = await HostedFormBrowser().inspect(job.canonical_url)
        plan = build_submission_plan(snapshot, profile, application_id=application.id, approval_id=approval.id, document_version=document.version, verification_id=verification.id, answers_hash=answers_hash, profile_hash=profile_hash, job_url=job.canonical_url, artifact_paths={"cv_pdf_path": document.cv_pdf_path, "cover_letter_pdf_path": document.cover_letter_pdf_path}, artifact_hashes={"cv_pdf_path": document.file_hashes.get("cv.pdf", ""), "cover_letter_pdf_path": document.file_hashes.get("cover-letter.pdf", "")})
    except SubmissionBlocked as exc:
        session.rollback()
        with session.begin():
            session.add(SubmissionAttempt(application_id=application.id, status="CONFIRMED_FAILURE", idempotency_key=secrets.token_urlsafe(32), started_at=datetime.now(timezone.utc), finished_at=datetime.now(timezone.utc), result_metadata={"reason_code": exc.reason_code, "reason": str(exc), "submission_clicked": False}))
        return RedirectResponse(f"/applications/{application_id}", status_code=303)
    session.rollback()
    with session.begin():
        locked = session.scalar(select(Application).where(Application.id == application_id).with_for_update())
        prior = session.scalar(select(SubmissionAttempt).where(SubmissionAttempt.application_id == application_id, SubmissionAttempt.status.in_(["PREPARED", "IN_PROGRESS", "OUTCOME_UNKNOWN", "CONFIRMED_SUCCESS"])).with_for_update())
        if not locked or locked.state != ApplicationState.APPROVED.value or locked.submission_lock or prior:
            raise HTTPException(status_code=409, detail="A submission attempt is already active or the application changed")
        session.add(SubmissionAttempt(application_id=application_id, status="PREPARED", idempotency_key=secrets.token_urlsafe(32), started_at=datetime.now(timezone.utc), result_metadata={"plan": plan.model_dump(mode="json"), "stage": "PREPARED", "submission_clicked": False}))
    return RedirectResponse(f"/applications/{application_id}", status_code=303)


@router.post("/applications/{application_id}/submit", include_in_schema=False)
async def submit_application(application_id: UUID, request: Request, session: Session = Depends(get_db)):
    if not _is_authenticated(request):
        return _login_redirect()
    data = await _form(request)
    _check_csrf(request, data)
    if data.get("confirm") != ["yes"]:
        raise HTTPException(status_code=422, detail="Explicit submission confirmation is required")
    try:
        attempt_id = UUID(data.get("attempt_id", [""])[0])
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Prepared submission is invalid") from exc
    with session.begin():
        application = session.scalar(select(Application).where(Application.id == application_id).with_for_update())
        attempt = session.scalar(select(SubmissionAttempt).where(SubmissionAttempt.id == attempt_id, SubmissionAttempt.application_id == application_id).with_for_update())
        if not application or application.state != ApplicationState.APPROVED.value or application.submission_lock or not attempt or attempt.status != "PREPARED":
            raise HTTPException(status_code=409, detail="This submission is no longer prepared. Do not retry automatically.")
        approval = session.get(ApplicationApproval, UUID(attempt.result_metadata["plan"]["approval_id"]))
        doc = session.scalar(select(ApplicationDocument).where(ApplicationDocument.application_id == application_id, ApplicationDocument.version == attempt.result_metadata["plan"]["document_version"]))
        plan = SubmissionPlan.model_validate(attempt.result_metadata["plan"])
        job = session.get(Job, application.job_id)
        verification = _latest_verification(session, job.id)
        questions = list(session.scalars(select(ScreeningQuestion).where(ScreeningQuestion.application_id == application_id).order_by(ScreeningQuestion.id)))
        answers_hash = screening_answers_hash([{"question_id": str(q.id), "status": q.status, "answer": q.answer, "profile_path": q.answer_profile_path} for q in questions])
        profile = _profile()
        profile_hash = hashlib.sha256(json.dumps(profile.model_dump(mode="json"), sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        if not approval or approval.invalidated_at or not doc or not verification or verification.classification != "VERIFIED" or str(verification.id) != plan.verification_id or doc.version != plan.document_version or approval.answers_hash != answers_hash or plan.answers_hash != answers_hash or plan.profile_hash != profile_hash or doc.profile_hash != profile_hash:
            raise HTTPException(status_code=409, detail="Reviewed data changed. Prepare the form again after reviewing the application.")
        attempt.status = "IN_PROGRESS"
        attempt.result_metadata = {**attempt.result_metadata, "stage": "SUBMITTING", "submission_clicked": False}
        application.submission_lock = True
    plan_paths = {"cv_pdf_path": doc.cv_pdf_path, "cover_letter_pdf_path": doc.cover_letter_pdf_path}
    try:
        result = await HostedFormBrowser().submit(plan, profile, plan_paths)
    except SubmissionBlocked as exc:
        with session.begin():
            application = session.scalar(select(Application).where(Application.id == application_id).with_for_update())
            attempt = session.scalar(select(SubmissionAttempt).where(SubmissionAttempt.id == attempt_id).with_for_update())
            application.submission_lock = False
            attempt.status = "CONFIRMED_FAILURE"
            attempt.finished_at = datetime.now(timezone.utc)
            attempt.result_metadata = {"reason_code": exc.reason_code, "reason": str(exc), "submission_clicked": False}
        return RedirectResponse(f"/applications/{application_id}", status_code=303)
    with session.begin():
        application = session.scalar(select(Application).where(Application.id == application_id).with_for_update())
        attempt = session.scalar(select(SubmissionAttempt).where(SubmissionAttempt.id == attempt_id).with_for_update())
        status = result.status
        attempt.status = status
        attempt.finished_at = datetime.now(timezone.utc)
        metadata = {"reason_code": result.reason_code, "submission_clicked": result.submission_clicked, "confirmation_number": result.confirmation_number, "confirmation_text": result.confirmation_marker}
        if result.screenshot_bytes:
            root = Path(get_settings().artifacts_path).expanduser().resolve()
            folder = root / str(application_id) / "submission-evidence"
            folder.mkdir(parents=True, exist_ok=True)
            evidence = folder / f"{attempt_id}.png"
            evidence.write_bytes(result.screenshot_bytes)
            metadata["screenshot_path"] = str(evidence)
        attempt.result_metadata = metadata
        if result.status == "CONFIRMED_SUCCESS":
            application.submission_lock = False
            change = transition(ApplicationState.APPROVED, ApplicationState.SUBMITTED, "dashboard-user", "Hosted form showed explicit application confirmation")
            application.state = change.new.value
            application.version += 1
            session.add(ApplicationTransition(application_id=application.id, previous_state=change.previous.value, new_state=change.new.value, actor="dashboard-user", reason=change.reason, metadata_json={"attempt_id": str(attempt.id)}))
            session.add(AuditEvent(event_type="application.submission.confirmed", actor="dashboard-user", application_id=application.id, payload={"attempt_id": str(attempt.id), "confirmation_number": result.confirmation_number}))
            session.flush()
            schedule_confirmed_submission(session, application.id, attempt.finished_at)
        elif result.status == "CONFIRMED_FAILURE" and not result.submission_clicked:
            application.submission_lock = False
        else:
            application.submission_lock = True
            attempt.status = "OUTCOME_UNKNOWN"
    return RedirectResponse(f"/applications/{application_id}", status_code=303)


@router.post("/applications/{application_id}/reconcile-submission", include_in_schema=False)
async def reconcile_submission(application_id: UUID, request: Request, session: Session = Depends(get_db)):
    if not _is_authenticated(request):
        return _login_redirect()
    data = await _form(request)
    _check_csrf(request, data)
    if data.get("reconcile_confirm") != ["yes"]:
        raise HTTPException(status_code=422, detail="Confirm that you checked the employer site")
    ref = data.get("evidence_ref", [""])[0].strip()[:500]
    if not ref or data.get("resolution", [""])[0] not in {"submitted", "not_submitted"}:
        raise HTTPException(status_code=422, detail="A confirmed outcome and reconciliation note are required")
    try:
        attempt_id = UUID(data.get("attempt_id", [""])[0])
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Submission attempt is invalid") from exc
    with session.begin():
        application = session.scalar(select(Application).where(Application.id == application_id).with_for_update())
        attempt = session.scalar(select(SubmissionAttempt).where(SubmissionAttempt.id == attempt_id, SubmissionAttempt.application_id == application_id).with_for_update())
        if not application or not application.submission_lock or not attempt or attempt.status not in {"IN_PROGRESS", "OUTCOME_UNKNOWN"}:
            raise HTTPException(status_code=409, detail="There is no uncertain submission to reconcile")
        if data["resolution"][0] == "submitted":
            attempt.status = "CONFIRMED_SUCCESS"
            application.state = ApplicationState.SUBMITTED.value
            application.version += 1
            session.add(ApplicationTransition(application_id=application_id, previous_state=ApplicationState.APPROVED.value, new_state=ApplicationState.SUBMITTED.value, actor="dashboard-user", reason="User reconciled the hosted application as submitted", metadata_json={"attempt_id": str(attempt_id)}))
            event_type = "application.submission.reconciled_success"
        else:
            attempt.status = "CONFIRMED_FAILURE"
            event_type = "application.submission.reconciled_not_submitted"
        attempt.finished_at = datetime.now(timezone.utc)
        attempt.result_metadata = {**(attempt.result_metadata or {}), "reconciliation_reference": ref, "reconciled_by": "dashboard-user"}
        application.submission_lock = False
        session.add(AuditEvent(event_type=event_type, actor="dashboard-user", application_id=application_id, payload={"attempt_id": str(attempt_id)}))
        if data["resolution"][0] == "submitted":
            session.flush()
            schedule_confirmed_submission(session, application_id, attempt.finished_at)
    return RedirectResponse(f"/applications/{application_id}", status_code=303)


@router.get("/applications/{application_id}/submission-evidence/{attempt_id}", include_in_schema=False)
def submission_evidence(application_id: UUID, attempt_id: UUID, request: Request, session: Session = Depends(get_db)):
    redirect = _require_user(request)
    if redirect:
        return redirect
    attempt = session.scalar(select(SubmissionAttempt).where(SubmissionAttempt.id == attempt_id, SubmissionAttempt.application_id == application_id))
    path_value = (attempt.result_metadata or {}).get("screenshot_path") if attempt else None
    if not path_value:
        raise HTTPException(status_code=404, detail="Submission evidence not found")
    root = Path(get_settings().artifacts_path).expanduser().resolve()
    path = Path(path_value).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise HTTPException(status_code=404, detail="Submission evidence not found")
    return FileResponse(path, media_type="image/png", filename="submission-evidence.png", headers={"Cache-Control": "no-store"})


@router.post("/applications/{application_id}/notes", include_in_schema=False)
async def add_application_note(application_id: UUID, request: Request, session: Session = Depends(get_db)):
    if not _is_authenticated(request):
        return _login_redirect()
    data = await _form(request)
    _check_csrf(request, data)
    body = data.get("body", [""])[0].strip()
    if not body or len(body) > 5000:
        raise HTTPException(status_code=422, detail="A note is required and must be at most 5,000 characters")
    with session.begin():
        application = session.scalar(select(Application).where(Application.id == application_id).with_for_update())
        if application is None:
            raise HTTPException(status_code=404, detail="Application not found")
        note = ApplicationNote(application_id=application_id, body=body, author="dashboard-user")
        session.add(note)
        session.flush()
        session.add(AuditEvent(event_type="application.note.created", actor="dashboard-user", application_id=application_id, payload={"note_id": str(note.id)}))
    return RedirectResponse(f"/applications/{application_id}", status_code=303)


@router.post("/applications/{application_id}/follow-ups/{reminder_id}/save-draft", include_in_schema=False)
async def save_follow_up_draft(application_id: UUID, reminder_id: UUID, request: Request, session: Session = Depends(get_db)):
    if not _is_authenticated(request):
        return _login_redirect()
    data = await _form(request)
    _check_csrf(request, data)
    draft_text = data.get("draft_text", [""])[0].strip()
    if not draft_text or len(draft_text) > 5000:
        raise HTTPException(status_code=422, detail="Draft must contain 1 to 5,000 characters")
    with session.begin():
        reminder = session.scalar(select(FollowUpReminder).where(FollowUpReminder.id == reminder_id, FollowUpReminder.application_id == application_id).with_for_update())
        if not reminder or reminder.status != "DRAFTED":
            raise HTTPException(status_code=409, detail="Only a draft awaiting review can be edited")
        reminder.draft_text = draft_text
        session.add(AuditEvent(event_type="application.follow_up.draft_edited", actor="dashboard-user", application_id=application_id, payload={"reminder_id": str(reminder_id)}))
    return RedirectResponse(f"/applications/{application_id}", status_code=303)


@router.post("/applications/{application_id}/follow-ups/{reminder_id}/approve", include_in_schema=False)
async def approve_follow_up_draft(application_id: UUID, reminder_id: UUID, request: Request, session: Session = Depends(get_db)):
    if not _is_authenticated(request):
        return _login_redirect()
    data = await _form(request)
    _check_csrf(request, data)
    if data.get("confirm") != ["yes"]:
        raise HTTPException(status_code=422, detail="Explicit draft approval is required")
    draft_text = data.get("draft_text", [""])[0].strip()
    if not draft_text or len(draft_text) > 5000:
        raise HTTPException(status_code=422, detail="Draft must contain 1 to 5,000 characters")
    with session.begin():
        reminder = session.scalar(select(FollowUpReminder).where(FollowUpReminder.id == reminder_id, FollowUpReminder.application_id == application_id).with_for_update())
        if not reminder or reminder.status != "DRAFTED":
            raise HTTPException(status_code=409, detail="This draft is no longer awaiting approval")
        reminder.draft_text = draft_text
        reminder.approved_at = datetime.now(timezone.utc)
        reminder.status = "APPROVED"
        session.add(AuditEvent(event_type="application.follow_up.draft_approved", actor="dashboard-user", application_id=application_id, payload={"reminder_id": str(reminder_id)}))
    return RedirectResponse(f"/applications/{application_id}", status_code=303)


@router.post("/applications/{application_id}/follow-ups/{reminder_id}/complete", include_in_schema=False)
async def complete_follow_up(application_id: UUID, reminder_id: UUID, request: Request, session: Session = Depends(get_db)):
    if not _is_authenticated(request):
        return _login_redirect()
    data = await _form(request)
    _check_csrf(request, data)
    if data.get("confirm") != ["yes"]:
        raise HTTPException(status_code=422, detail="Confirm that you handled the follow-up outside the dashboard")
    completion_note = data.get("completion_note", [""])[0].strip()[:1000]
    with session.begin():
        application = session.scalar(select(Application).where(Application.id == application_id).with_for_update())
        reminder = session.scalar(select(FollowUpReminder).where(FollowUpReminder.id == reminder_id, FollowUpReminder.application_id == application_id).with_for_update())
        if not application or not reminder or reminder.status != "APPROVED":
            raise HTTPException(status_code=409, detail="Only an approved draft can be marked handled")
        now = datetime.now(timezone.utc)
        reminder.status = "COMPLETED"
        reminder.completed_at = now
        reminder.completion_note = completion_note or None
        if application.state == ApplicationState.SUBMITTED.value:
            change = transition(ApplicationState.SUBMITTED, ApplicationState.FOLLOW_UP, "dashboard-user", "Follow-up handled manually")
            application.state = change.new.value
            application.version += 1
            session.add(ApplicationTransition(application_id=application_id, previous_state=change.previous.value, new_state=change.new.value, actor="dashboard-user", reason=change.reason, metadata_json={"reminder_id": str(reminder_id)}))
        session.add(AuditEvent(event_type="application.follow_up.completed", actor="dashboard-user", application_id=application_id, payload={"reminder_id": str(reminder_id)}))
    return RedirectResponse(f"/applications/{application_id}", status_code=303)
