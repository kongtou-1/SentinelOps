"""Explicit response ownership, notification delivery and maintenance policies."""

import sqlalchemy as sa
from alembic import op

revision = "0027_incident_response"
down_revision = "0026_stability_impact"
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
        "users", sa.Column("notification_email", sa.String(254), nullable=False, server_default="")
    )
    op.create_table(
        "notification_policy",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("managed", sa.Boolean(), nullable=False),
        sa.Column("email_enabled", sa.Boolean(), nullable=False),
        sa.Column("configured_by", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        *timestamps(),
    )
    op.create_table(
        "incident_responses",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "incident_id", sa.Uuid(), sa.ForeignKey("incidents.id"), nullable=False, unique=True
        ),
        sa.Column("snapshot", sa.JSON(), nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True)),
        sa.Column("accepted_by", sa.Uuid(), sa.ForeignKey("users.id")),
        sa.Column("escalated_at", sa.DateTime(timezone=True)),
        *timestamps(),
    )
    op.create_table(
        "notifications",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("incident_id", sa.Uuid(), sa.ForeignKey("incidents.id")),
        sa.Column("kind", sa.String(40), nullable=False),
        sa.Column("resource_kind", sa.String(40), nullable=False),
        sa.Column("resource_id", sa.Uuid(), nullable=False),
        sa.Column("dedupe_key", sa.String(250), nullable=False, unique=True),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("detail", sa.JSON(), nullable=False),
        sa.Column("read_at", sa.DateTime(timezone=True)),
        sa.Column("email", sa.String(254), nullable=False),
        sa.Column("policy_version", sa.Integer()),
        sa.Column("mail_status", sa.String(20), nullable=False),
        sa.Column("dispatched_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("error_code", sa.String(80)),
        sa.CheckConstraint(
            "mail_status IN ('disabled','queued','sending','sent','failed','unknown')",
            name="mail_status",
        ),
        *timestamps(),
    )
    for name in ("user_id", "incident_id"):
        op.create_index("ix_notifications_" + name, "notifications", [name])
    op.create_table(
        "maintenance_windows",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("scope_kind", sa.String(20), nullable=False),
        sa.Column("scope_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("reason", sa.String(2000), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.CheckConstraint("ends_at > starts_at", name="time_range"),
        sa.CheckConstraint("scope_kind IN ('service','server','rule')", name="scope"),
        sa.CheckConstraint("kind IN ('maintenance','silence')", name="kind"),
        *timestamps(),
    )
    op.create_index("ix_maintenance_windows_scope_id", "maintenance_windows", ["scope_id"])
    op.create_table(
        "alert_inhibitions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("parent_rule_id", sa.Uuid(), sa.ForeignKey("alert_rules.id"), nullable=False),
        sa.Column("child_rule_id", sa.Uuid(), sa.ForeignKey("alert_rules.id"), nullable=False),
        sa.Column("created_by", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.UniqueConstraint("parent_rule_id", "child_rule_id"),
        sa.CheckConstraint("parent_rule_id <> child_rule_id", name="different_rules"),
        *timestamps(),
    )
    op.create_table(
        "operations_audit_logs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id")),
        sa.Column("action", sa.String(60), nullable=False),
        sa.Column("resource_kind", sa.String(40), nullable=False),
        sa.Column("resource_id", sa.Uuid()),
        sa.Column("detail", sa.JSON(), nullable=False),
        *timestamps(),
    )
    for table in (
        "notification_policy",
        "incident_responses",
        "notifications",
        "maintenance_windows",
        "alert_inhibitions",
        "operations_audit_logs",
    ):
        op.execute(
            f"CREATE TRIGGER trg_{table}_updated_at BEFORE UPDATE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION set_updated_at()"
        )


def downgrade():
    for table in (
        "operations_audit_logs",
        "alert_inhibitions",
        "maintenance_windows",
        "notifications",
        "incident_responses",
        "notification_policy",
    ):
        op.drop_table(table)
    op.drop_column("users", "notification_email")
