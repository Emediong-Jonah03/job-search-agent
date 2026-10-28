from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import Application, AuditEvent, FollowUpReminder, Job
from app.domain.models import ApplicationState
from app.tracking.drafts import build_follow_up_draft

def schedule_confirmed_submission(
    session: Session,
    application_id: UUID,
    submitted_at: datetime | None = None,
) -> FollowUpReminder | None:
    """Create the one follow-up reminder for a confirmed submitted application."""
    application = session.scalar(select(Application).where(Application.id == application_id).with_for_update())
    if application is None or application.state != ApplicationState.SUBMITTED.value:
        return None
    existing = session.scalar(select(FollowUpReminder).where(FollowUpReminder.application_id == application_id).with_for_update())
    if existing:
        return existing
    submitted_at = submitted_at or datetime.now(timezone.utc)
    if submitted_at.tzinfo is None:
        submitted_at = submitted_at.replace(tzinfo=timezone.utc)
    reminder = FollowUpReminder(
        application_id=application_id,
        submitted_at=submitted_at,
        scheduled_for=submitted_at + timedelta(days=get_settings().follow_up_days),
        status="SCHEDULED",
    )
    session.add(reminder)
    session.flush()
    session.add(AuditEvent(
        event_type="application.follow_up.scheduled",
        actor="system",
        application_id=application_id,
        payload={"reminder_id": str(reminder.id), "scheduled_for": reminder.scheduled_for.isoformat()},
    ))
    return reminder


def draft_for_reminder(session: Session, reminder: FollowUpReminder, full_name: str) -> str:
    application = session.get(Application, reminder.application_id)
    job = session.get(Job, application.job_id) if application else None
    if not application or not job or application.state not in {ApplicationState.SUBMITTED.value, ApplicationState.FOLLOW_UP.value}:
        raise ValueError("A follow-up draft requires a confirmed submitted application")
    return build_follow_up_draft(job.employer, job.title, reminder.submitted_at, full_name)
