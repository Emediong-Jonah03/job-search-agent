from pathlib import Path

from alembic import op

revision = "0001_foundation"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    sql = Path(__file__).with_suffix(".sql").read_text(encoding="utf-8")
    op.execute(sql)


def downgrade() -> None:
    for table in ("submission_attempts", "audit_events", "application_transitions", "applications", "job_sources", "jobs"):
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")

