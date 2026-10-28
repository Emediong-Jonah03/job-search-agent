import hashlib
import json
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import Application, ApplicationApproval, ApplicationDocument, ApplicationTransition, AuditEvent, Job
from app.documents.models import ApplicationDocumentContent
from app.documents.claim_validation import validate_document_claims
from app.documents.rendering import render_document_files
from app.documents.storage import PrivateArtifactStorage
from app.domain.models import ApplicationState, Profile
from app.domain.state_machine import transition


class DocumentRevisionError(ValueError):
    pass


def revise_application_document(
    session: Session,
    document_id: UUID,
    content: ApplicationDocumentContent,
    profile: Profile,
    *,
    storage: PrivateArtifactStorage | None = None,
    expected_application_id: UUID | None = None,
) -> ApplicationDocument:
    """Validate edited structured source and persist a new immutable document version."""
    if session.in_transaction():
        raise DocumentRevisionError("Use a clean database session for document revision")
    original = session.get(ApplicationDocument, document_id)
    if original is None:
        raise DocumentRevisionError("Document not found")
    application_id = original.application_id
    if expected_application_id is not None and application_id != expected_application_id:
        session.rollback()
        raise DocumentRevisionError("Document does not belong to this application")
    app = session.get(Application, application_id)
    if app is None:
        raise DocumentRevisionError("Application not found")
    if app.submission_lock:
        session.rollback()
        raise DocumentRevisionError("Submission outcome must be reconciled before editing documents")
    allowed = {ApplicationState.CV_GENERATED.value, ApplicationState.AWAITING_APPROVAL.value, ApplicationState.APPROVED.value}
    if app.state not in allowed:
        raise DocumentRevisionError("Documents can no longer be edited in this application state")
    job = session.get(Job, app.job_id)
    if job is None or content.cover_letter.employer != job.employer or content.cover_letter.role != job.title:
        session.rollback()
        raise DocumentRevisionError("Cover-letter employer and role must remain tied to the saved job")
    files, _extracted, cv_text = render_document_files(profile, content)
    report = validate_document_claims(profile, content, cv_text)
    hashes = {name: hashlib.sha256(data).hexdigest() for name, data in files.items()}
    storage = storage or PrivateArtifactStorage(get_settings().artifacts_path)
    profile_payload = json.dumps(profile.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    profile_hash = hashlib.sha256(profile_payload.encode("utf-8")).hexdigest()
    provider = original.llm_provider
    model = original.llm_model
    session.rollback()

    with session.begin():
        locked = session.scalar(select(Application).where(Application.id == application_id).with_for_update())
        if locked is None or locked.state not in allowed or locked.submission_lock:
            raise DocumentRevisionError("Application state changed while editing")
        version = session.scalar(select(func.coalesce(func.max(ApplicationDocument.version), 0)).where(
            ApplicationDocument.application_id == application_id
        )) + 1
        stored = storage.write_version(application_id, version, files)
        revised = ApplicationDocument(
            application_id=application_id,
            version=version,
            cv_pdf_path=stored["cv.pdf"],
            cv_docx_path=stored["cv.docx"],
            cover_letter_pdf_path=stored["cover-letter.pdf"],
            cover_letter_docx_path=stored["cover-letter.docx"],
            file_hashes=hashes,
            profile_hash=profile_hash,
            claim_validation=report.model_dump(mode="json"),
            job_analysis=content.job_analysis.model_dump(mode="json"),
            content_json=content.model_dump(mode="json"),
            llm_provider=provider,
            llm_model=model,
        )
        session.add(revised)
        session.add(AuditEvent(
            event_type="application.documents.revised",
            actor="dashboard-user",
            application_id=application_id,
            payload={"version": version, "file_hashes": hashes},
        ))
        active_approval = session.scalar(select(ApplicationApproval).where(
            ApplicationApproval.application_id == application_id,
            ApplicationApproval.invalidated_at.is_(None),
        ).with_for_update())
        if active_approval:
            active_approval.invalidated_at = datetime.now(timezone.utc)
            active_approval.invalidation_reason = "A new document version was created"
        current = ApplicationState(locked.state)
        if current != ApplicationState.CV_GENERATED:
            change = transition(current, ApplicationState.CV_GENERATED, "dashboard-user", "Document edited; prior approval invalidated")
            locked.state = change.new.value
            locked.version += 1
            session.add(ApplicationTransition(
                application_id=application_id,
                previous_state=change.previous.value,
                new_state=change.new.value,
                actor=change.actor,
                reason=change.reason,
                metadata_json={"document_version": version},
            ))
        session.flush()
        session.refresh(revised)
    return revised
