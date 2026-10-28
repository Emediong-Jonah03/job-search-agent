from pathlib import Path

from alembic import op

revision = "0003_documents"
down_revision = "0002_verification"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(Path(__file__).with_suffix(".sql").read_text(encoding="utf-8"))


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS application_documents CASCADE")
