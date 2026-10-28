from alembic import op

revision = "0004_dashboard"
down_revision = "0003_documents"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE applications ADD COLUMN match_details jsonb NOT NULL DEFAULT '{}'::jsonb")
    op.execute("ALTER TABLE application_documents ADD COLUMN content_json jsonb NOT NULL DEFAULT '{}'::jsonb")
    op.execute("""
        CREATE TABLE screening_questions (
            id uuid PRIMARY KEY,
            application_id uuid NOT NULL REFERENCES applications(id) ON DELETE CASCADE,
            question text NOT NULL,
            proposed_answer text NOT NULL DEFAULT '',
            answer text NOT NULL DEFAULT '',
            status varchar(20) NOT NULL DEFAULT 'UNRESOLVED'
                CHECK (status IN ('UNRESOLVED', 'RESOLVED', 'NEEDS_CLARIFICATION')),
            answer_profile_path varchar(300),
            reviewed_by varchar(200),
            reviewed_at timestamptz
        )
    """)
    op.execute("CREATE INDEX ix_screening_questions_application ON screening_questions(application_id, status)")
    op.execute("""
        CREATE TABLE application_approvals (
            id uuid PRIMARY KEY,
            application_id uuid NOT NULL REFERENCES applications(id) ON DELETE CASCADE,
            document_version integer NOT NULL CHECK (document_version > 0),
            verification_id uuid NOT NULL REFERENCES job_verifications(id) ON DELETE RESTRICT,
            answers_hash varchar(64) NOT NULL,
            approved_by varchar(200) NOT NULL,
            approved_at timestamptz NOT NULL DEFAULT now(),
            invalidated_at timestamptz,
            invalidation_reason text
        )
    """)
    op.execute("CREATE INDEX ix_application_approvals_application_time ON application_approvals(application_id, approved_at)")
    op.execute("CREATE UNIQUE INDEX uq_active_application_approval ON application_approvals(application_id) WHERE invalidated_at IS NULL")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS screening_questions CASCADE")
    op.execute("DROP TABLE IF EXISTS application_approvals CASCADE")
    op.execute("ALTER TABLE application_documents DROP COLUMN IF EXISTS content_json")
    op.execute("ALTER TABLE applications DROP COLUMN IF EXISTS match_details")
