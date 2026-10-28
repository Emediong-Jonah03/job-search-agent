from alembic import op

revision = "0005_submission_safety"
down_revision = "0004_dashboard"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE applications ADD COLUMN submission_lock boolean NOT NULL DEFAULT false")
    op.execute("ALTER TABLE submission_attempts DROP CONSTRAINT ck_submission_status")
    op.execute("ALTER TABLE submission_attempts ADD CONSTRAINT ck_submission_status CHECK (status IN ('NOT_ATTEMPTED','PREPARED','IN_PROGRESS','CONFIRMED_SUCCESS','CONFIRMED_FAILURE','OUTCOME_UNKNOWN'))")


def downgrade() -> None:
    op.execute("ALTER TABLE submission_attempts DROP CONSTRAINT ck_submission_status")
    op.execute("ALTER TABLE submission_attempts ADD CONSTRAINT ck_submission_status CHECK (status IN ('NOT_ATTEMPTED','IN_PROGRESS','CONFIRMED_SUCCESS','CONFIRMED_FAILURE','OUTCOME_UNKNOWN'))")
    op.execute("ALTER TABLE applications DROP COLUMN submission_lock")
