from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.audit.redaction import redact
from app.db.models import Application, AuditEvent, Job, JobSource
from app.discovery.models import DiscoveredJob, LinkCheck
from app.discovery.normalization import identity_parts, normalize_url
from app.matching.scorer import MatchResult


def persist_discovery(
    session: Session,
    job: DiscoveredJob,
    link_check: LinkCheck,
    match: MatchResult,
) -> tuple[str, bool]:
    """Insert one eligible listing with a PostgreSQL uniqueness guard."""
    canonical = normalize_url(str(job.original_url))
    employer_key, title_key = identity_parts(job.employer, job.title)
    with session.begin():
        statement = (
            insert(Job)
            .values(
                id=uuid4(),
                employer=job.employer,
                employer_key=employer_key,
                title=job.title,
                title_key=title_key,
                canonical_url=canonical,
                source_url=str(job.source_url),
                posted_at=job.posted_at,
                description=job.description,
            )
            .on_conflict_do_nothing(constraint="uq_job_identity")
            .returning(Job.id)
        )
        new_id = session.execute(statement).scalar_one_or_none()
        created = new_id is not None
        job_id = new_id or session.scalar(
            select(Job.id).where(
                Job.canonical_url == canonical,
                Job.employer_key == employer_key,
                Job.title_key == title_key,
            )
        )
        if job_id is None:  # Defensive: concurrent delete or DB inconsistency.
            raise RuntimeError("Job insert conflicted but the canonical job could not be loaded")

        existing_job = session.get(Job, job_id)
        if not created and existing_job is not None and existing_job.deleted_at is not None:
            return str(job_id), False

        source_row = JobSource(
            job_id=job_id,
            adapter=job.source_name,
            source_url=str(job.source_url),
            evidence=redact({"source_id": job.source_id, "link_check": link_check.model_dump(mode="json"), "match": match.model_dump()}),
        )
        session.add(source_row)
        if created:
            application = Application(
                id=uuid4(), job_id=job_id, state="DISCOVERED", match_score=match.score,
                match_details=match.model_dump(mode="json"),
            )
            session.add(application)
            session.flush()
            application_id = application.id
        else:
            application_id = None
        session.add(AuditEvent(
            event_type="job.discovered" if created else "job.source_observed",
            actor=f"discovery:{job.source_name}",
            application_id=application_id,
            payload=redact({"job_id": str(job_id), "source": job.source_name, "link_accessible": link_check.accessible, "match_score": match.score}),
        ))
        session.flush()
    return str(job_id), created

