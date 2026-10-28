import hashlib
import json
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import Application, ApplicationDocument, ApplicationTransition, AuditEvent, Job
from app.documents.builder import build_document_content
from app.documents.claim_validation import validate_document_claims
from app.documents.rendering import render_document_files
from app.documents.storage import PrivateArtifactStorage
from app.domain.models import ApplicationState, Profile
from app.domain.state_machine import transition
from app.discovery.models import DiscoveredJob
from app.llm.factory import get_llm_provider
from app.llm.base import LLMProvider


class DocumentGenerationError(RuntimeError):
    pass


def _profile_fingerprint(profile: Profile) -> str:
    payload = json.dumps(profile.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


async def generate_application_documents(
    session: Session,
    application_id: UUID,
    profile: Profile,
    *,
    provider: LLMProvider | None = None,
    storage: PrivateArtifactStorage | None = None,
) -> ApplicationDocument:
    """Generate validated files for a shortlisted application and persist an immutable version."""
    if session.in_transaction():
        raise DocumentGenerationError("Use a clean database session for document generation")
    application = session.scalar(
        select(Application).where(Application.id == application_id)
    )
    if application is None:
        raise DocumentGenerationError("Application not found")
    if application.state not in {ApplicationState.SHORTLISTED.value, ApplicationState.CV_GENERATED.value}:
        raise DocumentGenerationError("Documents can only be generated for shortlisted applications")
    job = session.get(Job, application.job_id)
    if job is None:
        raise DocumentGenerationError("Application job not found")

    provider = provider or get_llm_provider()
    storage = storage or PrivateArtifactStorage(get_settings().artifacts_path)
    result = await provider.analyze_job_description(job.description or "")
    discovered = DiscoveredJob(
        employer=job.employer,
        title=job.title,
        original_url=job.canonical_url,
        source_url=job.source_url,
        description=job.description,
        posted_at=job.posted_at,
        source_name="stored-job",
        source_id=str(job.id),
    )
    content = build_document_content(profile, discovered, result.analysis)
    files, _extracted, cv_text = render_document_files(profile, content)
    report = validate_document_claims(profile, content, cv_text)
    if not report.passed:
        raise DocumentGenerationError("Claim validation failed; no files were stored")

    hashes = {name: hashlib.sha256(data).hexdigest() for name, data in files.items()}
    # End the read transaction before opening the short, locked write transaction.
    session.rollback()
    with session.begin():
        locked = session.scalar(select(Application).where(Application.id == application_id).with_for_update())
        if locked is None:
            raise DocumentGenerationError("Application not found")
        current_state = ApplicationState(locked.state)
        if current_state not in {ApplicationState.SHORTLISTED, ApplicationState.CV_GENERATED}:
            raise DocumentGenerationError("Application state changed during document generation")
        version = session.scalar(
            select(func.coalesce(func.max(ApplicationDocument.version), 0)).where(
                ApplicationDocument.application_id == application_id
            )
        ) + 1
        stored_paths = storage.write_version(application_id, version, files)
        document = ApplicationDocument(
            application_id=application_id,
            version=version,
            cv_pdf_path=stored_paths["cv.pdf"],
            cv_docx_path=stored_paths["cv.docx"],
            cover_letter_pdf_path=stored_paths["cover-letter.pdf"],
            cover_letter_docx_path=stored_paths["cover-letter.docx"],
            file_hashes=hashes,
            profile_hash=_profile_fingerprint(profile),
            claim_validation=report.model_dump(mode="json"),
            job_analysis=result.analysis.model_dump(mode="json"),
            content_json=content.model_dump(mode="json"),
            llm_provider=result.provider,
            llm_model=result.model,
        )
        session.add(document)
        session.add(AuditEvent(
            event_type="application.documents.generated",
            actor="document-agent",
            application_id=application_id,
            payload={
                "version": version,
                "provider": result.provider,
                "model": result.model,
                "claim_validation_passed": report.passed,
                "checked_claim_count": report.checked_claim_count,
                "file_hashes": hashes,
            },
        ))
        session.add(AuditEvent(
            event_type="application.job_description.analyzed",
            actor="document-agent",
            application_id=application_id,
            payload={
                "provider": result.provider,
                "model": result.model,
                "required_skill_count": len(result.analysis.required_skills),
                "preferred_skill_count": len(result.analysis.preferred_skills),
                "keyword_count": len(result.analysis.keywords),
            },
        ))
        if current_state == ApplicationState.SHORTLISTED:
            change = transition(current_state, ApplicationState.CV_GENERATED, "document-agent", "Validated application documents generated")
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
        session.refresh(document)
    return document
