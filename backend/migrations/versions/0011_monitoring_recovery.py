"""Collection boundaries and evidence-based alert recovery (no historical success backfill)."""

import sqlalchemy as sa
from alembic import op

revision = "0011_monitoring_recovery"
down_revision = "0010_postmortems"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("servers", sa.Column("docker_state_target", sa.String(300)))
    op.add_column("servers", sa.Column("collection_config_changed_at", sa.DateTime(timezone=True)))
    # Old predictions lack a reliable source boundary; the migration invalidates them.
    op.execute("UPDATE servers SET collection_config_changed_at = clock_timestamp()")
    op.alter_column(
        "servers", "collection_config_changed_at", nullable=False, server_default=sa.func.now()
    )
    op.add_column(
        "alert_events", sa.Column("rule_snapshot", sa.JSON(), nullable=False, server_default="{}")
    )
    op.add_column(
        "alert_events",
        sa.Column("recovery_status", sa.String(20), nullable=False, server_default="pending"),
    )
    op.add_column("alert_events", sa.Column("recovery_reason", sa.String(100)))
    op.add_column("alert_events", sa.Column("recovery_checked_at", sa.DateTime(timezone=True)))
    op.add_column("alert_events", sa.Column("recovery_evidence_at", sa.DateTime(timezone=True)))
    op.execute("UPDATE alert_events SET recovery_reason = 'historical_unverified'")
    op.create_index(
        "ix_alert_events_recovery_pending",
        "alert_events",
        ["recovery_checked_at"],
        postgresql_where=sa.text(
            "status = 'resolved' AND recovery_status = 'pending' AND deleted_at IS NULL"
        ),
    )


def downgrade():
    op.drop_index("ix_alert_events_recovery_pending", table_name="alert_events")
    for column in (
        "recovery_evidence_at",
        "recovery_checked_at",
        "recovery_reason",
        "recovery_status",
        "rule_snapshot",
    ):
        op.drop_column("alert_events", column)
    op.drop_column("servers", "collection_config_changed_at")
    op.drop_column("servers", "docker_state_target")
