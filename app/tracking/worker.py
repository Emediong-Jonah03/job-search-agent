from datetime import datetime, timezone

from sqlalchemy import exists, select

from app.config import get_settings
from app.db.models import Application, AuditEvent, FollowUpReminder, Job, SubmissionAttempt
from app.db.session import SessionLocal
from app.domain.models import ApplicationState
from app.profile.loader import load_profile
from app.tracking.drafts import build_follow_up_draft
from app.tracking.follow_ups import schedule_confirmed_submission


def refresh_follow_up_reminders(ctx: dict | None = None) -> int:
    """Idempotently backfill confirmed submissions and create due dashboard drafts."""
    del ctx
    now = datetime.now(timezone.utc)
    settings = get_settings()
    profile = load_profile(settings.profile_path)
    processed = 0
    with SessionLocal() as session, session.begin():
        confirmed_apps = session.scalars(
            select(Application)
            .where(
                Application.state == ApplicationState.SUBMITTED.value,
                ~exists().where(FollowUpReminder.application_id == Application.id),
                exists().where(
                    SubmissionAttempt.application_id == Application.id,
                    SubmissionAttempt.status == "CONFIRMED_SUCCESS",
                ),
            )
            .order_by(Application.updated_at)
            .limit(200)
            .with_for_update(skip_locked=True)
        )
        for application in confirmed_apps:
            attempt = session.scalar(
                select(SubmissionAttempt)
                .where(SubmissionAttempt.application_id == application.id, SubmissionAttempt.status == "CONFIRMED_SUCCESS")
                .order_by(SubmissionAttempt.finished_at.desc().nullslast(), SubmissionAttempt.started_at.desc().nullslast())
                .limit(1)
            )
            submitted_at = (attempt.finished_at or attempt.started_at) if attempt else now
            schedule_confirmed_submission(session, application.id, submitted_at)

        reminders = session.scalars(
            select(FollowUpReminder)
            .join(Application, Application.id == FollowUpReminder.application_id)
            .where(
                FollowUpReminder.status == "SCHEDULED",
                FollowUpReminder.scheduled_for <= now,
                Application.state.in_([ApplicationState.SUBMITTED.value, ApplicationState.FOLLOW_UP.value]),
            )
            .order_by(FollowUpReminder.scheduled_for)
            .limit(200)
            .with_for_update(skip_locked=True)
        )
        for reminder in reminders:
            application = session.get(Application, reminder.application_id)
            job = session.get(Job, application.job_id) if application else None
            if not application or not job:
                continue
            reminder.draft_text = build_follow_up_draft(job.employer, job.title, reminder.submitted_at, profile.full_name or "[Your name]")
            reminder.drafted_at = now
            reminder.status = "DRAFTED"
            session.add(AuditEvent(
                event_type="application.follow_up.draft_created",
                actor="system",
                application_id=application.id,
                payload={"reminder_id": str(reminder.id)},
            ))
            processed += 1
    return processed
