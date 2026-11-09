"""Frozen repair handoffs and append-only development/verification records."""

import sqlalchemy as sa
from alembic import op

revision = "0015_repair_handoffs"
down_revision = "0014_diagnosis_sources"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "repair_handoffs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("incident_id", sa.Uuid(), sa.ForeignKey("incidents.id"), nullable=False),
        sa.Column("analysis_id", sa.Uuid(), sa.ForeignKey("agent_analyses.id"), nullable=False),
        sa.Column("application_id", sa.Uuid(), sa.ForeignKey("applications.id")),
        sa.Column("requested_by", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("packet", sa.JSON(), nullable=False),
        sa.Column("packet_hash", sa.String(64), nullable=False),
        sa.Column("results", sa.JSON(), nullable=False),
        sa.Column("verifications", sa.JSON(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_repair_handoffs_incident_id", "repair_handoffs", ["incident_id"])
    op.execute(
        "CREATE TRIGGER trg_repair_handoffs_updated_at BEFORE UPDATE ON repair_handoffs "
        "FOR EACH ROW EXECUTE FUNCTION set_updated_at()"
    )


def downgrade():
    op.drop_table("repair_handoffs")
