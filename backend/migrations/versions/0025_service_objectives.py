"""Service objectives and durable worker observations."""

import sqlalchemy as sa
from alembic import op

revision = "0025_service_objectives"
down_revision = "0024_service_databases"
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
    op.create_table(
        "service_objectives",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("application_id", sa.Uuid(), sa.ForeignKey("applications.id"), nullable=False),
        sa.Column("monitor_id", sa.Uuid(), sa.ForeignKey("service_monitors.id")),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("kind", sa.String(30), nullable=False),
        sa.Column("operation", sa.String(100), nullable=False),
        sa.Column("target", sa.Float(), nullable=False),
        sa.Column("window_days", sa.Integer(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("evaluation", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("checked_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("window_days IN (1,7,30)", name="window"),
        sa.CheckConstraint(
            "kind IN ('business_success','probe_availability','request_p95','llm_ttft_p95')",
            name="kind",
        ),
        sa.CheckConstraint("target > 0", name="target"),
        *timestamps(),
    )
    op.create_index(
        "ix_service_objectives_application_id", "service_objectives", ["application_id"]
    )
    op.create_table(
        "worker_heartbeats",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("state", sa.String(30), nullable=False),
        sa.Column("detail", sa.JSON(), nullable=False, server_default="{}"),
        *timestamps(),
    )
    for table in ("service_objectives", "worker_heartbeats"):
        op.execute(
            f"CREATE TRIGGER trg_{table}_updated_at BEFORE UPDATE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION set_updated_at()"
        )


def downgrade():
    op.drop_table("worker_heartbeats")
    op.drop_table("service_objectives")
