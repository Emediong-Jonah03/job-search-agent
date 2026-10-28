from datetime import datetime, timezone


def build_follow_up_draft(employer: str, role: str, submitted_at: datetime, full_name: str) -> str:
    submitted_day = submitted_at.astimezone(timezone.utc).date().isoformat()
    return (
        f"Hello {employer} recruiting team,\n\n"
        f"I’m following up on my application for the {role} position, submitted on {submitted_day}. "
        "Please let me know if you need any additional information.\n\n"
        f"Best,\n{full_name}"
    )
