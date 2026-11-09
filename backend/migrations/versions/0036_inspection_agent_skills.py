"""Agent-led inspections and versioned Skill Python scripts. Preserve old plans."""

import sqlalchemy as sa
from alembic import op

revision = "0036_inspection_agent_skills"
down_revision = "0035_inspection_center"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "inspection_plans",
        sa.Column("execution_mode", sa.String(20), nullable=False, server_default="records"),
    )
    op.add_column(
        "inspection_plans",
        sa.Column("agent_source_ids", sa.JSON(), nullable=False, server_default="[]"),
    )
    op.add_column(
        "inspection_plans",
        sa.Column("agent_parameters", sa.JSON(), nullable=False, server_default="{}"),
    )
    op.add_column(
        "knowledge_skills", sa.Column("scripts", sa.JSON(), nullable=False, server_default="{}")
    )


def downgrade():
    op.drop_column("knowledge_skills", "scripts")
    for name in ("agent_parameters", "agent_source_ids", "execution_mode"):
        op.drop_column("inspection_plans", name)
