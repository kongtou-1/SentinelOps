"""Unified checks and durable object/check execution. Historical results are untouched."""

import sqlalchemy as sa
from alembic import op

revision = "0040_inspection_checks"
down_revision = "0039_user_resource_policies"
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
        "knowledge_skills", sa.Column("resources", sa.JSON(), nullable=False, server_default="{}")
    )
    op.create_table(
        "inspection_http_connections",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("application_id", sa.Uuid(), sa.ForeignKey("applications.id")),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("environment", sa.String(50), nullable=False),
        sa.Column("base_url", sa.String(2000), nullable=False),
        sa.Column("encrypted_headers", sa.Text()),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        *timestamps(),
    )
    op.create_index(
        "ix_inspection_http_connections_application_id",
        "inspection_http_connections",
        ["application_id"],
    )
    op.add_column(
        "inspection_plans", sa.Column("check_items", sa.JSON(), nullable=False, server_default="[]")
    )
    op.add_column(
        "inspection_plans",
        sa.Column("fixed_objects", sa.JSON(), nullable=False, server_default="[]"),
    )
    op.add_column(
        "inspection_plans",
        sa.Column("object_mode", sa.String(20), nullable=False, server_default="dynamic"),
    )
    op.add_column("inspection_items", sa.Column("check_status", sa.String(20)))
    op.create_table(
        "inspection_check_results",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("run_id", sa.Uuid(), sa.ForeignKey("inspection_runs.id"), nullable=False),
        sa.Column("plan_id", sa.Uuid(), sa.ForeignKey("inspection_plans.id"), nullable=False),
        sa.Column("item_id", sa.Uuid(), sa.ForeignKey("inspection_items.id")),
        sa.Column("object_key", sa.String(200), nullable=False),
        sa.Column("check_id", sa.String(50), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("scope", sa.String(20), nullable=False),
        sa.Column("execution_status", sa.String(20), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("severity", sa.String(20)),
        sa.Column("summary", sa.String(4000), nullable=False),
        sa.Column("result", sa.JSON(), nullable=False),
        sa.Column("error_code", sa.String(80)),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        *timestamps(),
    )
    op.create_index(
        "uq_inspection_check_result",
        "inspection_check_results",
        ["run_id", "object_key", "check_id"],
        unique=True,
    )
    for column in ("run_id", "plan_id", "item_id"):
        op.create_index(
            f"ix_inspection_check_results_{column}", "inspection_check_results", [column]
        )
    op.create_table(
        "inspection_traces",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("run_id", sa.Uuid(), sa.ForeignKey("inspection_runs.id"), nullable=False),
        sa.Column("plan_id", sa.Uuid(), sa.ForeignKey("inspection_plans.id"), nullable=False),
        sa.Column(
            "check_result_id",
            sa.Uuid(),
            sa.ForeignKey("inspection_check_results.id"),
            nullable=False,
        ),
        sa.Column("tool", sa.String(80), nullable=False),
        sa.Column("success", sa.Boolean(), nullable=False),
        sa.Column("data", sa.JSON(), nullable=False),
        *timestamps(),
    )
    for column in ("run_id", "plan_id", "check_result_id"):
        op.create_index(f"ix_inspection_traces_{column}", "inspection_traces", [column])


def downgrade():
    op.drop_table("inspection_traces")
    op.drop_table("inspection_check_results")
    op.drop_table("inspection_http_connections")
    op.drop_column("knowledge_skills", "resources")
    op.drop_column("inspection_items", "check_status")
    for column in ("object_mode", "fixed_objects", "check_items"):
        op.drop_column("inspection_plans", column)
