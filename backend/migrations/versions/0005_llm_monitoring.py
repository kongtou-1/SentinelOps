"""LLM resources and alert references."""

import sqlalchemy as sa
from alembic import op

revision = "0005_llm_monitoring"
down_revision = "0004_alerts"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "llm_endpoints",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("name", sa.String(100), nullable=False, unique=True),
        sa.Column("provider", sa.String(20), nullable=False),
        sa.Column("base_url", sa.String(2000), nullable=False),
        sa.Column("model", sa.String(100), nullable=False),
        sa.Column("encrypted_api_key", sa.Text(), nullable=False),
        sa.Column("probe_enabled", sa.Boolean(), nullable=False),
        sa.Column("probe_interval_seconds", sa.Integer(), nullable=False),
        sa.Column("last_probe_at", sa.DateTime(timezone=True)),
        sa.Column("last_probe_success", sa.Boolean()),
        sa.Column("last_error_type", sa.String(40)),
        sa.Column("last_http_status", sa.Integer()),
        sa.Column("last_latency_ms", sa.Float()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
    )
    op.execute(
        "CREATE TRIGGER trg_llm_endpoints_updated_at BEFORE UPDATE ON llm_endpoints "
        "FOR EACH ROW EXECUTE FUNCTION set_updated_at()"
    )
    for table in ("alert_rules", "alert_events"):
        op.add_column(table, sa.Column("llm_endpoint_id", sa.Uuid()))
        op.create_foreign_key(
            f"fk_{table}_llm_endpoint_id_llm_endpoints",
            table,
            "llm_endpoints",
            ["llm_endpoint_id"],
            ["id"],
        )
    op.create_index("ix_alert_events_llm_endpoint_id", "alert_events", ["llm_endpoint_id"])


def downgrade():
    for table in ("alert_events", "alert_rules"):
        op.drop_column(table, "llm_endpoint_id")
    op.drop_table("llm_endpoints")
