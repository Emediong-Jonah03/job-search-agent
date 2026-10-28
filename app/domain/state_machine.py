from dataclasses import dataclass

from app.domain.models import ApplicationState


class InvalidTransition(ValueError):
    pass


TRANSITIONS: dict[ApplicationState, frozenset[ApplicationState]] = {
    ApplicationState.DISCOVERED: frozenset({ApplicationState.VERIFIED, ApplicationState.REJECTED_BY_VERIFICATION, ApplicationState.SKIPPED}),
    ApplicationState.VERIFIED: frozenset({ApplicationState.SHORTLISTED, ApplicationState.REJECTED_BY_VERIFICATION, ApplicationState.SKIPPED}),
    ApplicationState.SHORTLISTED: frozenset({ApplicationState.CV_GENERATED, ApplicationState.REJECTED_BY_VERIFICATION, ApplicationState.SKIPPED}),
    ApplicationState.CV_GENERATED: frozenset({ApplicationState.AWAITING_APPROVAL, ApplicationState.SHORTLISTED, ApplicationState.REJECTED_BY_VERIFICATION, ApplicationState.SKIPPED}),
    ApplicationState.AWAITING_APPROVAL: frozenset({ApplicationState.APPROVED, ApplicationState.CV_GENERATED, ApplicationState.REJECTED_BY_VERIFICATION, ApplicationState.SKIPPED}),
    ApplicationState.APPROVED: frozenset({ApplicationState.SUBMITTED, ApplicationState.CV_GENERATED, ApplicationState.REJECTED_BY_VERIFICATION, ApplicationState.SKIPPED}),
    ApplicationState.SUBMITTED: frozenset({ApplicationState.FOLLOW_UP}),
    ApplicationState.FOLLOW_UP: frozenset(),
    ApplicationState.REJECTED_BY_VERIFICATION: frozenset(),
    ApplicationState.SKIPPED: frozenset(),
}


@dataclass(frozen=True)
class Transition:
    previous: ApplicationState
    new: ApplicationState
    actor: str
    reason: str | None = None


def transition(current: ApplicationState, target: ApplicationState, actor: str, reason: str | None = None) -> Transition:
    if target not in TRANSITIONS[current]:
        raise InvalidTransition(f"Transition {current.value} -> {target.value} is not allowed")
    if target == ApplicationState.APPROVED and not actor.strip():
        raise InvalidTransition("Approval requires an authenticated actor")
    return Transition(previous=current, new=target, actor=actor, reason=reason)

