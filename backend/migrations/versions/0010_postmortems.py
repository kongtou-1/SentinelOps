"""Reviewed, versioned postmortems and one-attempt email deliveries."""

import sqlalchemy as sa
from alembic import op

revision = "0010_postmortems"
down_revision = "0009_approvals_execution"
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


def foreign(name, table, nullable=False):
    return sa.Column(name, sa.Uuid(), sa.ForeignKey(table + ".id"), nullable=nullable)


def upgrade():
    op.create_table(
        "postmortem_reports",
        sa.Column("id", sa.Uuid(), primary_key=True),
        foreign("incident_id", "incidents"),
        foreign("requested_by", "users"),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("snapshot", sa.JSON(), nullable=False),
        sa.Column("snapshot_hash", sa.String(64), nullable=False),
        sa.Column("config", sa.JSON(), nullable=False),
        sa.Column("tool_calls", sa.JSON(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("error_code", sa.String(50)),
        foreign("reviewed_by", "users", True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True)),
        sa.Column("review_comment", sa.String(2000)),
        sa.CheckConstraint(
            "status IN ('queued','running','draft','approved','failed','interrupted')",
            name="status",
        ),
        *timestamps(),
    )
    op.create_index(
        "uq_postmortem_reports_active_incident",
        "postmortem_reports",
        ["incident_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('queued','running') AND deleted_at IS NULL"),
    )
    op.create_table(
        "postmortem_revisions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        foreign("report_id", "postmortem_reports"),
        sa.Column("revision", sa.Integer(), nullable=False),
        foreign("author_id", "users", True),
        sa.Column("content", sa.JSON(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.UniqueConstraint("report_id", "revision"),
        *timestamps(),
    )
    op.create_table(
        "postmortem_deliveries",
        sa.Column("id", sa.Uuid(), primary_key=True),
        foreign("report_id", "postmortem_reports"),
        sa.Column("revision", sa.Integer(), nullable=False),
        foreign("requested_by", "users"),
        sa.Column("idempotency_key", sa.Uuid(), nullable=False, unique=True),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("recipients", sa.JSON(), nullable=False),
        sa.Column("sender", sa.String(254), nullable=False),
        sa.Column("subject", sa.String(300), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("comment", sa.String(2000), nullable=False),
        sa.Column("attachment", sa.LargeBinary(), nullable=False),
        sa.Column("attachment_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("dispatched_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("error_code", sa.String(50)),
        sa.CheckConstraint(
            "status IN ('queued','sending','sent','failed','interrupted','unknown')", name="status"
        ),
        *timestamps(),
    )
    for table, column in (
        ("postmortem_reports", "incident_id"),
        ("postmortem_revisions", "report_id"),
        ("postmortem_deliveries", "report_id"),
    ):
        op.create_index(f"ix_{table}_{column}", table, [column])
        op.execute(
            f"CREATE TRIGGER trg_{table}_updated_at BEFORE UPDATE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION set_updated_at()"
        )
    op.alter_column("audit_logs", "approval_id", nullable=True)
    op.add_column("audit_logs", foreign("report_id", "postmortem_reports", True))
    op.create_index("ix_audit_logs_report_id", "audit_logs", ["report_id"])


def downgrade():
    # Destructive rollback is tested only on the dedicated test database.
    op.execute("DELETE FROM audit_logs WHERE report_id IS NOT NULL")
    op.drop_index("ix_audit_logs_report_id", table_name="audit_logs")
    op.drop_column("audit_logs", "report_id")
    op.alter_column("audit_logs", "approval_id", nullable=False)
    for table in ("postmortem_deliveries", "postmortem_revisions", "postmortem_reports"):
        op.drop_table(table)
