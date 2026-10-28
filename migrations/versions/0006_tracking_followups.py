from alembic import op

revision = "0006_tracking_followups"
down_revision = "0005_submission_safety"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE follow_up_reminders (
            id uuid PRIMARY KEY,
            application_id uuid NOT NULL REFERENCES applications(id) ON DELETE RESTRICT,
            submitted_at timestamptz NOT NULL,
            scheduled_for timestamptz NOT NULL,
            status varchar(20) NOT NULL DEFAULT 'SCHEDULED'
                CHECK (status IN ('SCHEDULED','DRAFTED','APPROVED','COMPLETED','CANCELLED')),
            draft_text text,
            drafted_at timestamptz,
            approved_at timestamptz,
            completed_at timestamptz,
            completion_note text,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_follow_up_application UNIQUE (application_id),
            CHECK (status NOT IN ('DRAFTED','APPROVED','COMPLETED') OR draft_text IS NOT NULL),
            CHECK (status != 'APPROVED' OR approved_at IS NOT NULL),
            CHECK (status != 'COMPLETED' OR completed_at IS NOT NULL)
        )
    """)
    op.execute("CREATE INDEX ix_follow_up_status_due ON follow_up_reminders(status, scheduled_for)")
    op.execute("""
        CREATE TABLE application_notes (
            id uuid PRIMARY KEY,
            application_id uuid NOT NULL REFERENCES applications(id) ON DELETE CASCADE,
            body text NOT NULL CHECK (length(body) BETWEEN 1 AND 5000),
            author varchar(200) NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now()
        )
    """)
    op.execute("CREATE INDEX ix_application_notes_application_time ON application_notes(application_id, created_at)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS application_notes CASCADE")
    op.execute("DROP TABLE IF EXISTS follow_up_reminders CASCADE")
