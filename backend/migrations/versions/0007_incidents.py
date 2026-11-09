"""Incident aggregation and append-only handling history."""

import sqlalchemy as sa
from alembic import op

revision = "0007_incidents"
down_revision = "0006_predictions"
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
        "incidents",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("severity", sa.String(20), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("assignee_id", sa.Uuid(), sa.ForeignKey("users.id")),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_alert_started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("recovered_at", sa.DateTime(timezone=True)),
        sa.Column("closed_at", sa.DateTime(timezone=True)),
        sa.Column("was_in_progress", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("merged_into_id", sa.Uuid(), sa.ForeignKey("incidents.id")),
        sa.CheckConstraint(
            "status IN ('open', 'in_progress', 'recovered', 'closed', 'merged')", name="status"
        ),
        sa.CheckConstraint("severity IN ('warning', 'critical')", name="severity"),
        *timestamps(),
    )
    op.create_index("ix_incidents_assignee_id", "incidents", ["assignee_id"])
    op.create_table(
        "incident_alerts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("incident_id", sa.Uuid(), sa.ForeignKey("incidents.id"), nullable=False),
        sa.Column("alert_event_id", sa.Uuid(), sa.ForeignKey("alert_events.id"), nullable=False),
        sa.UniqueConstraint("alert_event_id"),
        *timestamps(),
    )
    op.create_index("ix_incident_alerts_incident_id", "incident_alerts", ["incident_id"])
    op.create_table(
        "incident_timeline",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("incident_id", sa.Uuid(), sa.ForeignKey("incidents.id"), nullable=False),
        sa.Column("event_type", sa.String(40), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("actor_type", sa.String(20), nullable=False),
        sa.Column("actor_id", sa.Uuid(), sa.ForeignKey("users.id")),
        sa.Column("content", sa.JSON(), nullable=False),
        *timestamps(),
    )
    op.create_index("ix_incident_timeline_incident_id", "incident_timeline", ["incident_id"])
    for table in ("incidents", "incident_alerts", "incident_timeline"):
        op.execute(
            f"CREATE TRIGGER trg_{table}_updated_at BEFORE UPDATE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION set_updated_at()"
        )


def downgrade():
    op.drop_table("incident_timeline")
    op.drop_table("incident_alerts")
    op.drop_table("incidents")
