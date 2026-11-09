"""Service probes, traffic binding, and the missing LLM binding timestamp trigger."""

import sqlalchemy as sa
from alembic import op

revision = "0022_service_observability"
down_revision = "0021_auto_diagnosis"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        "CREATE TRIGGER trg_llm_service_bindings_updated_at BEFORE UPDATE ON llm_service_bindings "
        "FOR EACH ROW EXECUTE FUNCTION set_updated_at()"
    )
    op.add_column("servers", sa.Column("blackbox_exporter_target", sa.String(300)))
    op.add_column("applications", sa.Column("traffic_target", sa.String(300)))
    op.add_column(
        "applications",
        sa.Column(
            "traffic_config_changed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_table(
        "service_monitors",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("application_id", sa.Uuid(), sa.ForeignKey("applications.id"), nullable=False),
        sa.Column("container_id", sa.Uuid(), sa.ForeignKey("containers.id")),
        sa.Column("probe_server_id", sa.Uuid(), sa.ForeignKey("servers.id")),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("protocol", sa.String(10), nullable=False),
        sa.Column("target", sa.String(2000), nullable=False),
        sa.Column("method", sa.String(10), nullable=False),
        sa.Column("route", sa.String(200), nullable=False),
        sa.Column("dns_server", sa.String(300), nullable=False),
        sa.Column("importance", sa.String(20), nullable=False),
        sa.Column("expected_status", sa.Integer(), nullable=False),
        sa.Column("assertions", sa.JSON(), nullable=False),
        sa.Column("encrypted_request", sa.Text()),
        sa.Column("interval_seconds", sa.Integer(), nullable=False),
        sa.Column("timeout_seconds", sa.Integer(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column(
            "config_changed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("last_probe_at", sa.DateTime(timezone=True)),
        sa.Column("last_probe_success", sa.Boolean()),
        sa.Column("last_error_code", sa.String(50)),
        sa.Column("last_http_status", sa.Integer()),
        sa.Column("last_latency_seconds", sa.Float()),
        sa.Column("last_success_at", sa.DateTime(timezone=True)),
        sa.Column("last_failure_at", sa.DateTime(timezone=True)),
        sa.Column("last_evidence", sa.JSON(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_service_monitors_application_id", "service_monitors", ["application_id"])
    op.execute(
        "CREATE TRIGGER trg_service_monitors_updated_at BEFORE UPDATE ON service_monitors "
        "FOR EACH ROW EXECUTE FUNCTION set_updated_at()"
    )
    for table in ("alert_rules", "alert_events"):
        op.add_column(table, sa.Column("service_monitor_id", sa.Uuid()))
        op.create_foreign_key(
            f"fk_{table}_service_monitor_id_service_monitors",
            table,
            "service_monitors",
            ["service_monitor_id"],
            ["id"],
        )
    op.create_index("ix_alert_events_service_monitor_id", "alert_events", ["service_monitor_id"])


def downgrade():
    op.drop_index("ix_alert_events_service_monitor_id", table_name="alert_events")
    for table in ("alert_rules", "alert_events"):
        op.drop_constraint(
            f"fk_{table}_service_monitor_id_service_monitors", table, type_="foreignkey"
        )
        op.drop_column(table, "service_monitor_id")
    op.drop_table("service_monitors")
    op.drop_column("applications", "traffic_config_changed_at")
    op.drop_column("applications", "traffic_target")
    op.drop_column("servers", "blackbox_exporter_target")
    op.execute("DROP TRIGGER trg_llm_service_bindings_updated_at ON llm_service_bindings")
