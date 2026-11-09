"""Freeze source identities for both diagnostic entry points."""

import sqlalchemy as sa
from alembic import op

revision = "0014_diagnosis_sources"
down_revision = "0013_application_sources"
branch_labels = None
depends_on = None


def upgrade():
    for table in ("agent_analyses", "analysis_runs"):
        op.add_column(
            table,
            sa.Column("source_context", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        )


def downgrade():
    for table in ("analysis_runs", "agent_analyses"):
        op.drop_column(table, "source_context")
