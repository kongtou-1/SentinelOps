"""Allow LLM endpoints to reference a managed Agent channel credential."""

import sqlalchemy as sa
from alembic import op

revision = "0043_llm_agent_channel"
down_revision = "0042_independent_databases"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("llm_endpoints", sa.Column("agent_channel_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        op.f("fk_llm_endpoints_agent_channel_id_agent_channels"),
        "llm_endpoints",
        "agent_channels",
        ["agent_channel_id"],
        ["id"],
    )
    op.alter_column("llm_endpoints", "encrypted_api_key", existing_type=sa.Text(), nullable=True)
    op.create_check_constraint(
        op.f("ck_llm_endpoints_key_source"),
        "llm_endpoints",
        "(encrypted_api_key IS NULL) <> (agent_channel_id IS NULL)",
    )


def downgrade():
    # Refuse to downgrade while an endpoint has no independent key.
    op.alter_column("llm_endpoints", "encrypted_api_key", existing_type=sa.Text(), nullable=False)
    op.drop_constraint(op.f("ck_llm_endpoints_key_source"), "llm_endpoints", type_="check")
    op.drop_constraint(
        op.f("fk_llm_endpoints_agent_channel_id_agent_channels"),
        "llm_endpoints",
        type_="foreignkey",
    )
    op.drop_column("llm_endpoints", "agent_channel_id")
