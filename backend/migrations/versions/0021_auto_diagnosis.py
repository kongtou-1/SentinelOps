"""Configurable automatic incident diagnosis."""

import sqlalchemy as sa
from alembic import op

revision = "0021_auto_diagnosis"
down_revision = "0020_llm_service_bindings"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "diagnosis_policy",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("configured_by", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("id = 1", name="singleton"),
    )
    op.execute(
        "CREATE TRIGGER trg_diagnosis_policy_updated_at BEFORE UPDATE ON diagnosis_policy "
        "FOR EACH ROW EXECUTE FUNCTION set_updated_at()"
    )
    op.add_column("alert_rules", sa.Column("auto_diagnose", sa.Boolean()))
    op.add_column("alert_rules", sa.Column("auto_diagnose_by", sa.Uuid()))
    op.create_foreign_key(
        "fk_alert_rules_auto_diagnose_by_users",
        "alert_rules",
        "users",
        ["auto_diagnose_by"],
        ["id"],
    )
    op.add_column(
        "agent_analyses",
        sa.Column("trigger", sa.String(20), nullable=False, server_default="manual"),
    )
    op.create_check_constraint("trigger", "agent_analyses", "trigger IN ('manual','automatic')")
    op.create_index(
        "uq_agent_analyses_automatic_incident",
        "agent_analyses",
        ["incident_id"],
        unique=True,
        postgresql_where=sa.text("trigger = 'automatic'"),
    )


def downgrade():
    op.drop_index("uq_agent_analyses_automatic_incident", table_name="agent_analyses")
    op.drop_constraint(op.f("ck_agent_analyses_trigger"), "agent_analyses", type_="check")
    op.drop_column("agent_analyses", "trigger")
    op.drop_constraint("fk_alert_rules_auto_diagnose_by_users", "alert_rules", type_="foreignkey")
    op.drop_column("alert_rules", "auto_diagnose_by")
    op.drop_column("alert_rules", "auto_diagnose")
    op.drop_table("diagnosis_policy")
