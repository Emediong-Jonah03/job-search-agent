from sqlalchemy.orm import Session

from app.audit.redaction import redact
from app.db.models import AuditEvent


def record_event(session: Session, event_type: str, actor: str, payload: dict, application_id=None) -> AuditEvent:
    event = AuditEvent(event_type=event_type, actor=actor, payload=redact(payload), application_id=application_id)
    session.add(event)
    session.flush()
    return event

