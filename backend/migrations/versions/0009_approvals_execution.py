"""Durable, bound, one-use authorization and execution audit."""

import sqlalchemy as sa
from alembic import op

revision = "0009_approvals_execution"
down_revision = "0008_ai_knowledge"
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


def foreign(name, target, nullable=False, unique=False):
    return sa.Column(
        name, sa.Uuid(), sa.ForeignKey(target + ".id"), nullable=nullable, unique=unique
    )


def upgrade():
    op.create_table(
        "approvals",
        sa.Column("id", sa.Uuid(), primary_key=True),
        foreign("incident_id", "incidents"),
        foreign("analysis_id", "agent_analyses"),
        foreign("container_id", "containers"),
        foreign("requested_by", "users"),
        foreign("approver_id", "users", nullable=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("binding", sa.JSON(), nullable=False),
        sa.Column("binding_hash", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True)),
        sa.Column("decision_comment", sa.String(2000)),
        sa.Column("error_code", sa.String(50)),
        sa.CheckConstraint(
            "status IN ('pending','approved','rejected','expired','invalidated','consumed')",
            name="status",
        ),
        *timestamps(),
    )
    op.create_index(
        "uq_approvals_active_analysis",
        "approvals",
        ["analysis_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('pending','approved') AND deleted_at IS NULL"),
    )
    op.create_table(
        "skill_executions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        foreign("approval_id", "approvals", unique=True),
        foreign("incident_id", "incidents"),
        foreign("container_id", "containers"),
        foreign("initiated_by", "users"),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("binding_hash", sa.String(64), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("dispatched_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("precheck", sa.JSON(), nullable=False),
        sa.Column("output", sa.JSON(), nullable=False),
        sa.Column("verification", sa.JSON(), nullable=False),
        sa.Column("error_code", sa.String(50)),
        sa.CheckConstraint(
            "status IN ('queued','prechecking','executing','verifying',"
            "'succeeded','failed','interrupted','unknown')",
            name="status",
        ),
        *timestamps(),
    )
    op.create_index(
        "uq_skill_executions_active_container",
        "skill_executions",
        ["container_id"],
        unique=True,
        postgresql_where=sa.text(
            "status IN ('queued','prechecking','executing','verifying') AND deleted_at IS NULL"
        ),
    )
    op.create_table(
        "audit_logs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        foreign("user_id", "users", nullable=True),
        foreign("incident_id", "incidents"),
        foreign("approval_id", "approvals"),
        foreign("execution_id", "skill_executions", nullable=True),
        sa.Column("action", sa.String(50), nullable=False),
        sa.Column("request_id", sa.String(36), nullable=False),
        sa.Column("ip", sa.String(100)),
        sa.Column("detail", sa.JSON(), nullable=False),
        *timestamps(),
    )
    for table, columns in (
        ("approvals", ("incident_id",)),
        ("skill_executions", ("incident_id",)),
        ("audit_logs", ("incident_id", "approval_id")),
    ):
        for column in columns:
            op.create_index(f"ix_{table}_{column}", table, [column])
    for table in ("approvals", "skill_executions", "audit_logs"):
        op.execute(
            f"CREATE TRIGGER trg_{table}_updated_at BEFORE UPDATE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION set_updated_at()"
        )


def downgrade():
    for table in ("audit_logs", "skill_executions", "approvals"):
        op.drop_table(table)
