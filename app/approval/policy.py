import hashlib
import json
from typing import Any


def screening_answers_hash(answers: list[dict[str, Any]]) -> str:
    payload = json.dumps(answers, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def approval_snapshot_matches(
    expected_document_version: int,
    current_document_version: int,
    expected_verification_id: str,
    current_verification_id: str,
    expected_answers_hash: str,
    current_answers_hash: str,
) -> bool:
    return (
        expected_document_version == current_document_version
        and expected_verification_id == current_verification_id
        and expected_answers_hash == current_answers_hash
    )


def approval_blockers(
    claim_validation: dict[str, Any] | None,
    screening_statuses: list[str],
    verification_classification: str | None,
) -> list[str]:
    blockers: list[str] = []
    if not claim_validation or claim_validation.get("passed") is not True:
        blockers.append("The latest document version has not passed claim validation.")
    if verification_classification != "VERIFIED":
        blockers.append("The latest posting verification is not VERIFIED.")
    unresolved = sum(status != "RESOLVED" for status in screening_statuses)
    if unresolved:
        blockers.append(f"{unresolved} screening question(s) still need review.")
    return blockers
