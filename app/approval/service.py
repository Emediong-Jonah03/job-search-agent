from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.approval.policy import approval_blockers, approval_snapshot_matches, screening_answers_hash
from app.db.models import Application, ApplicationApproval, ApplicationDocument, ApplicationTransition, AuditEvent, JobVerification, ScreeningQuestion
from app.domain.models import ApplicationState
from app.domain.state_machine import transition


class ApprovalDenied(ValueError):
    pass


def _latest_document(session: Session, application_id: UUID) -> ApplicationDocument | None:
    return session.scalar(
        select(ApplicationDocument)
        .where(ApplicationDocument.application_id == application_id)
        .order_by(ApplicationDocument.version.desc())
        .limit(1)
    )


def _check_ready(session: Session, application_id: UUID):
    application = session.get(Application, application_id)
    verification = session.scalar(
        select(JobVerification)
        .where(JobVerification.job_id == application.job_id)
        .order_by(JobVerification.assessed_at.desc())
        .limit(1)
    ) if application else None
    document = _latest_document(session, application_id)
    answers = session.execute(select(
        ScreeningQuestion.id,
        ScreeningQuestion.status,
        ScreeningQuestion.answer,
        ScreeningQuestion.answer_profile_path,
    ).where(ScreeningQuestion.application_id == application_id).order_by(ScreeningQuestion.id)).all()
    statuses = [row.status for row in answers]
    blockers = approval_blockers(
        document.claim_validation if document else None,
        statuses,
        verification.classification if verification else None,
    )
    if blockers:
        raise ApprovalDenied(" ".join(blockers))
    if any(row.status == "RESOLVED" and (not row.answer or not row.answer_profile_path) for row in answers):
        raise ApprovalDenied("A resolved screening answer is missing its profile fact")
    if document is None or verification is None:
        raise ApprovalDenied("A validated document and verified posting are required")
    answer_payload = [
        {"question_id": str(row.id), "status": row.status, "answer": row.answer, "profile_path": row.answer_profile_path}
        for row in answers
    ]
    answers_hash = screening_answers_hash(answer_payload)
    return document, verification, answers_hash


def _record_transition(
    session: Session, application: Application, target: ApplicationState, actor: str, reason: str,
    metadata: dict | None = None,
) -> None:
    previous = ApplicationState(application.state)
    change = transition(previous, target, actor, reason)
    application.state = change.new.value
    application.version += 1
    session.add(ApplicationTransition(
        application_id=application.id,
        previous_state=change.previous.value,
        new_state=change.new.value,
        actor=actor,
        reason=reason,
        metadata_json=metadata or {},
    ))
    session.add(AuditEvent(
        event_type="application.state_transition",
        actor=actor,
        application_id=application.id,
        payload={"previous_state": change.previous.value, "new_state": change.new.value, "reason": reason, **(metadata or {})},
    ))


def submit_for_approval(session: Session, application_id: UUID, actor: str) -> Application:
    if session.in_transaction():
        raise ApprovalDenied("Use a clean database session")
    with session.begin():
        application = session.scalar(select(Application).where(Application.id == application_id).with_for_update())
        if application is None:
            raise ApprovalDenied("Application not found")
        if application.state != ApplicationState.CV_GENERATED.value:
            raise ApprovalDenied("Only a generated application can be submitted for review")
        document, verification, _answers_hash = _check_ready(session, application_id)
        _record_transition(
            session, application, ApplicationState.AWAITING_APPROVAL, actor,
            "Application sent for human review",
            {"document_version": document.version, "verification_id": str(verification.id)},
        )
        session.flush()
        return application


def approve_application(
    session: Session,
    application_id: UUID,
    actor: str,
    *,
    expected_document_version: int,
    expected_verification_id: UUID,
    expected_answers_hash: str,
) -> Application:
    if session.in_transaction():
        raise ApprovalDenied("Use a clean database session")
    with session.begin():
        application = session.scalar(select(Application).where(Application.id == application_id).with_for_update())
        if application is None:
            raise ApprovalDenied("Application not found")
        if application.state != ApplicationState.AWAITING_APPROVAL.value:
            raise ApprovalDenied("Application-specific review is required before approval")
        document, verification, answers_hash = _check_ready(session, application_id)
        if not approval_snapshot_matches(
            expected_document_version,
            document.version,
            str(expected_verification_id),
            str(verification.id),
            expected_answers_hash,
            answers_hash,
        ):
            raise ApprovalDenied("The reviewed document, verification, or screening answers changed. Reload and review again.")
        active = session.scalar(select(ApplicationApproval).where(
            ApplicationApproval.application_id == application_id,
            ApplicationApproval.invalidated_at.is_(None),
        ).with_for_update())
        if active:
            active.invalidated_at = datetime.now(timezone.utc)
            active.invalidation_reason = "Replaced by a new application-specific approval"
        approval = ApplicationApproval(
            application_id=application_id,
            document_version=document.version,
            verification_id=verification.id,
            answers_hash=answers_hash,
            approved_by=actor,
        )
        session.add(approval)
        session.flush()
        _record_transition(
            session, application, ApplicationState.APPROVED, actor,
            "User approved this application",
            {"approval_id": str(approval.id), "document_version": document.version,
             "verification_id": str(verification.id), "answers_hash": answers_hash},
        )
        session.add(AuditEvent(
            event_type="application.approved",
            actor=actor,
            application_id=application_id,
            payload={"approval_id": str(approval.id), "document_version": document.version,
                     "verification_id": str(verification.id), "answers_hash": answers_hash},
        ))
        session.flush()
        return application
