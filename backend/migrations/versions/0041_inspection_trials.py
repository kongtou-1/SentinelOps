"""Isolated form snapshots for sample inspection runs."""

import sqlalchemy as sa
from alembic import op

revision = "0041_inspection_trials"
down_revision = "0040_inspection_checks"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "inspection_plans",
        sa.Column("trial_only", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "inspection_runs",
        sa.Column("run_kind", sa.String(20), nullable=False, server_default="standard"),
    )


def downgrade():
    op.drop_column("inspection_runs", "run_kind")
    op.drop_column("inspection_plans", "trial_only")
