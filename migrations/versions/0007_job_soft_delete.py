from alembic import op


revision = "0007_job_soft_delete"
down_revision = "0006_tracking_followups"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE jobs ADD COLUMN deleted_at timestamptz")
    op.execute("CREATE INDEX ix_jobs_deleted_at ON jobs(deleted_at)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_jobs_deleted_at")
    op.execute("ALTER TABLE jobs DROP COLUMN IF EXISTS deleted_at")
