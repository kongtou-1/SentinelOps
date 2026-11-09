"""Periodic forecasts, durable runs and alert references."""

import sqlalchemy as sa
from alembic import op

revision = "0006_predictions"
down_revision = "0005_llm_monitoring"
branch_labels = None
depends_on = None


def timestamps():
    return [
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
    ]


def upgrade():
    op.add_column(
        "llm_endpoints",
        sa.Column(
            "probe_config_changed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    # Existing series may contain several provider configurations. Start a safe new epoch.
    op.create_table(
        "prediction_tasks",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("name", sa.String(100), nullable=False, unique=True),
        sa.Column("server_id", sa.Uuid(), sa.ForeignKey("servers.id")),
        sa.Column("container_id", sa.Uuid(), sa.ForeignKey("containers.id")),
        sa.Column("llm_endpoint_id", sa.Uuid(), sa.ForeignKey("llm_endpoints.id")),
        sa.Column("metric", sa.String(50), nullable=False),
        sa.Column("selector", sa.JSON(), nullable=False),
        sa.Column("threshold", sa.Float(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("interval_seconds", sa.Integer(), nullable=False),
        sa.Column(
            "next_run_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        *timestamps(),
        sa.CheckConstraint(
            "num_nonnulls(server_id, container_id, llm_endpoint_id) = 1", name="one_resource"
        ),
    )
    op.create_table(
        "prediction_results",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("task_id", sa.Uuid(), sa.ForeignKey("prediction_tasks.id"), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("trigger", sa.String(20), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("data_end_at", sa.DateTime(timezone=True)),
        sa.Column("error_code", sa.String(50)),
        *[
            sa.Column(field, sa.JSON(), nullable=False)
            for field in (
                "config",
                "quality",
                "horizons",
                "anomalies",
                "detector_state",
                "backtest",
            )
        ],
        *timestamps(),
    )
    op.create_index("ix_prediction_results_task_id", "prediction_results", ["task_id"])
    op.create_index(
        "uq_prediction_results_active_task",
        "prediction_results",
        ["task_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('queued', 'running') AND deleted_at IS NULL"),
    )
    for table in ("prediction_tasks", "prediction_results"):
        op.execute(
            f"CREATE TRIGGER trg_{table}_updated_at BEFORE UPDATE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION set_updated_at()"
        )
    for table in ("alert_rules", "alert_events"):
        op.add_column(table, sa.Column("prediction_task_id", sa.Uuid()))
        op.create_foreign_key(
            f"fk_{table}_prediction_task_id_prediction_tasks",
            table,
            "prediction_tasks",
            ["prediction_task_id"],
            ["id"],
        )
    op.add_column("alert_rules", sa.Column("prediction_horizon_hours", sa.Integer()))
    op.create_index("ix_alert_events_prediction_task_id", "alert_events", ["prediction_task_id"])


def downgrade():
    op.drop_column("alert_rules", "prediction_horizon_hours")
    for table in ("alert_events", "alert_rules"):
        op.drop_column(table, "prediction_task_id")
    op.drop_table("prediction_results")
    op.drop_table("prediction_tasks")
    op.drop_column("llm_endpoints", "probe_config_changed_at")
