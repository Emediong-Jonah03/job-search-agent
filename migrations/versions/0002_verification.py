from pathlib import Path

from alembic import op

revision = "0002_verification"
down_revision = "0001_foundation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(Path(__file__).with_suffix(".sql").read_text(encoding="utf-8"))


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS verification_signals CASCADE")
    op.execute("DROP TABLE IF EXISTS job_verifications CASCADE")

