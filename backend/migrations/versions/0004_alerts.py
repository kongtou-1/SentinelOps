"""0004_alerts."""

import sqlalchemy as sa
from alembic import op

revision = "0004_alerts"
down_revision = "0003_resources"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "alert_rules",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.String(length=100), nullable=False),
        sa.Column("server_id", sa.Uuid(), nullable=True),
        sa.Column("metric", sa.String(length=50), nullable=False),
        sa.Column("threshold", sa.Float(), nullable=False),
        sa.Column("for_seconds", sa.Integer(), nullable=False),
        sa.Column("severity", sa.String(length=20), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["server_id"], ["servers.id"], name=op.f("fk_alert_rules_server_id_servers")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_alert_rules")),
        sa.UniqueConstraint("code", name=op.f("uq_alert_rules_code")),
    )
    op.create_table(
        "alert_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column("rule_id", sa.Uuid(), nullable=True),
        sa.Column("server_id", sa.Uuid(), nullable=True),
        sa.Column("severity", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("labels", sa.JSON(), nullable=False),
        sa.Column("annotations", sa.JSON(), nullable=False),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("acknowledged_by", sa.Uuid(), nullable=True),
        sa.Column("comment", sa.String(length=2000), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["acknowledged_by"], ["users.id"], name=op.f("fk_alert_events_acknowledged_by_users")
        ),
        sa.ForeignKeyConstraint(
            ["rule_id"], ["alert_rules.id"], name=op.f("fk_alert_events_rule_id_alert_rules")
        ),
        sa.ForeignKeyConstraint(
            ["server_id"], ["servers.id"], name=op.f("fk_alert_events_server_id_servers")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_alert_events")),
        sa.UniqueConstraint("fingerprint", "starts_at", name=op.f("uq_alert_events_fingerprint")),
    )
    op.create_index(op.f("ix_alert_events_server_id"), "alert_events", ["server_id"], unique=False)
    for table in ("alert_rules", "alert_events"):
        op.execute(
            f"CREATE TRIGGER trg_{table}_updated_at BEFORE UPDATE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION set_updated_at()"
        )


def downgrade():
    op.drop_table("alert_events")
    op.drop_table("alert_rules")
