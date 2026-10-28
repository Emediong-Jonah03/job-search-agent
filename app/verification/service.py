from uuid import UUID, uuid4

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.audit.redaction import redact
from app.config import get_settings
from app.db.models import (
    Application,
    ApplicationTransition,
    AuditEvent,
    Job,
    JobSource,
    JobVerification,
    VerificationSignalRecord,
)
from app.domain.models import ApplicationState, VerificationClassification
from app.domain.state_machine import transition
from app.verification.engine import assess_evidence
from app.verification.models import VerificationEvidence


PRE_SUBMISSION_STATES = {
    ApplicationState.DISCOVERED,
    ApplicationState.VERIFIED,
    ApplicationState.SHORTLISTED,
    ApplicationState.CV_GENERATED,
    ApplicationState.AWAITING_APPROVAL,
    ApplicationState.APPROVED,
}


def verify_job(
    session: Session,
    job_id: UUID,
    *,
    verified_threshold: int | None = None,
    suspicious_risk_threshold: int | None = None,
) -> VerificationClassification:
    """Persist a point-in-time assessment, signals, audit event, and any required state change."""
    with session.begin():
        job = session.scalar(select(Job).where(Job.id == job_id))
        if job is None:
            raise LookupError(f"Job {job_id} does not exist")
        source = session.scalar(
            select(JobSource)
            .where(JobSource.job_id == job_id)
            .order_by(desc(JobSource.observed_at))
            .limit(1)
        )
        application = session.scalar(
            select(Application)
            .where(Application.job_id == job_id)
            .order_by(Application.created_at)
            .limit(1)
            .with_for_update()
        )
        if source is None:
            raise LookupError(f"Job {job_id} has no source observation")

        link_evidence = (source.evidence or {}).get("link_check") or {}
        assessment_input = VerificationEvidence(
            employer=job.employer,
            title=job.title,
            original_url=job.canonical_url,
            source_url=source.source_url,
            source_name=source.adapter,
            description=job.description,
            posted_at=job.posted_at,
            link_accessible=link_evidence.get("accessible"),
            observed_at=source.observed_at,
        )
        settings = get_settings()
        assessment = assess_evidence(
            assessment_input,
            verified_threshold=verified_threshold or settings.verification_verified_threshold,
            suspicious_risk_threshold=suspicious_risk_threshold or settings.verification_suspicious_risk_threshold,
        )
        verification_id = uuid4()
        session.add(JobVerification(
            id=verification_id,
            job_id=job.id,
            application_id=application.id if application else None,
            classification=assessment.classification.value,
            score=assessment.score,
            support_score=assessment.support_score,
            risk_score=assessment.risk_score,
            policy_version=assessment.policy_version,
            reasons=assessment.reasons,
            missing_evidence=assessment.missing_evidence,
            uncertainty=assessment.uncertainty,
            policy_parameters=assessment.policy_parameters,
            assessed_at=assessment.assessed_at,
        ))
        session.add_all([
            VerificationSignalRecord(
                id=uuid4(),
                verification_id=verification_id,
                name=signal.name,
                kind=signal.kind.value,
                weight=signal.weight,
                finding=signal.finding,
                evidence_url=str(signal.evidence_url) if signal.evidence_url else None,
                data_source=signal.data_source,
                reliability=signal.reliability,
                observed_at=signal.observed_at,
            )
            for signal in assessment.signals
        ])

        if application is not None:
            current = ApplicationState(application.state)
            target: ApplicationState | None = None
            if assessment.classification == VerificationClassification.SUSPICIOUS and current in PRE_SUBMISSION_STATES:
                target = ApplicationState.REJECTED_BY_VERIFICATION
            elif assessment.classification == VerificationClassification.VERIFIED and current == ApplicationState.DISCOVERED:
                target = ApplicationState.VERIFIED
            if target is not None:
                change = transition(current, target, "verification:policy", "; ".join(assessment.reasons))
                application.state = change.new.value
                application.version += 1
                session.add(ApplicationTransition(
                    id=uuid4(),
                    application_id=application.id,
                    previous_state=change.previous.value,
                    new_state=change.new.value,
                    actor=change.actor,
                    reason=change.reason,
                    metadata_json={"verification_id": str(verification_id), "policy_version": assessment.policy_version},
                ))

        session.add(AuditEvent(
            event_type="job.verification_assessed",
            actor="verification:deterministic-policy",
            application_id=application.id if application else None,
            payload=redact({
                "job_id": str(job.id),
                "verification_id": str(verification_id),
                "classification": assessment.classification.value,
                "score": assessment.score,
                "risk_score": assessment.risk_score,
                "policy_version": assessment.policy_version,
                "policy_parameters": assessment.policy_parameters,
            }),
        ))
        session.flush()
        return assessment.classification


def verify_unassessed_discovered_jobs(session_factory) -> int:
    """Assess discovered applications once, after their source evidence has been persisted."""
    from app.db.models import Application, JobVerification

    with session_factory() as session:
        job_ids = list(session.scalars(
            select(Job.id)
            .join(Application, Application.job_id == Job.id)
            .outerjoin(JobVerification, JobVerification.job_id == Job.id)
            .where(Application.state == ApplicationState.DISCOVERED.value, JobVerification.id.is_(None))
            .distinct()
        ))
    for job_id in job_ids:
        with session_factory() as session:
            verify_job(session, job_id)
    return len(job_ids)

