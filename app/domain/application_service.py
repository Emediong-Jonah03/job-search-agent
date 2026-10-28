from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit.redaction import redact
from app.db.models import Application, ApplicationTransition, AuditEvent
from app.domain.models import ApplicationState, StateTransitionRequest
from app.domain.state_machine import transition


class ApplicationNotFound(LookupError):
    pass


def transition_application(session: Session, application_id: UUID, request: StateTransitionRequest) -> Application:
    """Persist the state change and its audit record atomically under a row lock."""
    with session.begin():
        application = session.scalar(
            select(Application).where(Application.id == application_id).with_for_update()
        )
        if application is None:
            raise ApplicationNotFound(str(application_id))

        prior = ApplicationState(application.state)
        change = transition(prior, request.target, request.actor, request.reason)
        application.state = change.new.value
        application.version += 1

        transition_row = ApplicationTransition(
            application_id=application.id,
            previous_state=change.previous.value,
            new_state=change.new.value,
            actor=change.actor,
            reason=change.reason,
            metadata_json=redact(request.metadata),
        )
        audit_row = AuditEvent(
            event_type="application.state_transition",
            actor=request.actor,
            application_id=application.id,
            payload=redact(
                {"previous_state": change.previous.value, "new_state": change.new.value, "reason": change.reason}
            ),
        )
        session.add_all([transition_row, audit_row])
        session.flush()
        return application

