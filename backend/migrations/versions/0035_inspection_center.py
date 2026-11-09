"""Reusable sources, scheduling and independent rule outcomes. Preserve all history."""

import json
from uuid import uuid4

import sqlalchemy as sa
from alembic import op

revision = "0035_inspection_center"
down_revision = "0034_scoped_access"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "inspection_data_sources",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("application_id", sa.Uuid(), sa.ForeignKey("applications.id")),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("environment", sa.String(50), nullable=False),
        sa.Column("source_kind", sa.String(20), nullable=False),
        sa.Column("source_config", sa.JSON(), nullable=False),
        sa.Column("encrypted_headers", sa.Text()),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("last_test_at", sa.DateTime(timezone=True)),
        sa.Column("last_test_success", sa.Boolean()),
        sa.Column("last_error_code", sa.String(80)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
    )
    op.create_index(
        "ix_inspection_data_sources_application_id", "inspection_data_sources", ["application_id"]
    )
    op.execute(
        "CREATE TRIGGER trg_inspection_data_sources_updated_at BEFORE UPDATE ON "
        "inspection_data_sources FOR EACH ROW EXECUTE FUNCTION set_updated_at()"
    )
    for column in [
        sa.Column("data_source_id", sa.Uuid(), sa.ForeignKey("inspection_data_sources.id")),
        sa.Column("schedule_kind", sa.String(20), nullable=False, server_default="interval"),
        sa.Column("cron_expression", sa.String(100), nullable=False, server_default=""),
        sa.Column(
            "schedule_timezone", sa.String(100), nullable=False, server_default="Asia/Shanghai"
        ),
        sa.Column("scheduled_at", sa.DateTime(timezone=True)),
        sa.Column("timeout_seconds", sa.Integer(), nullable=False, server_default="300"),
        sa.Column("max_retries", sa.Integer(), nullable=False, server_default="0"),
    ]:
        op.add_column("inspection_plans", column)
    op.alter_column("inspection_plans", "next_run_at", nullable=True)
    for column in [
        sa.Column("error_stage", sa.String(30)),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("retry_at", sa.DateTime(timezone=True)),
    ]:
        op.add_column("inspection_runs", column)
    op.add_column("inspection_items", sa.Column("rule_verdict", sa.String(20)))
    # Copy encrypted ciphertext without ever decrypting it in a migration.
    connection = op.get_bind()
    plans = sa.table(
        "inspection_plans", sa.column("id", sa.Uuid()), sa.column("data_source_id", sa.Uuid())
    )
    sources = sa.table(
        "inspection_data_sources",
        *[
            sa.column(k, t)
            for k, t in [
                ("id", sa.Uuid()),
                ("application_id", sa.Uuid()),
                ("name", sa.String()),
                ("environment", sa.String()),
                ("source_kind", sa.String()),
                ("source_config", sa.JSON()),
                ("encrypted_headers", sa.Text()),
            ]
        ],
    )
    for row in connection.execute(
        sa.text("SELECT * FROM inspection_plans WHERE deleted_at IS NULL")
    ).mappings():
        identifier = uuid4()
        connection.execute(
            sources.insert().values(
                id=identifier,
                application_id=row["application_id"],
                name=row["name"][:90] + " · 数据源",
                environment=row["environment"],
                source_kind=row["source_kind"],
                source_config=row["source_config"],
                encrypted_headers=row["encrypted_headers"],
            )
        )
        connection.execute(
            plans.update().where(plans.c.id == row["id"]).values(data_source_id=identifier)
        )
    # The old verdict describes agreement with a prior decision, not rule health.
    items = sa.table(
        "inspection_items", sa.column("id", sa.Uuid()), sa.column("rule_verdict", sa.String())
    )
    for row in connection.execute(sa.text("SELECT id, checks FROM inspection_items")).mappings():
        checks = row["checks"] if isinstance(row["checks"], list) else json.loads(row["checks"])
        states = {c.get("status") for c in checks}
        verdict = "fail" if "fail" in states else "pass" if states == {"pass"} else "unknown"
        connection.execute(
            items.update().where(items.c.id == row["id"]).values(rule_verdict=verdict)
        )


def downgrade():
    op.drop_column("inspection_items", "rule_verdict")
    for column in ["error_stage", "retry_count", "retry_at"]:
        op.drop_column("inspection_runs", column)
    op.execute("UPDATE inspection_plans SET next_run_at=now() WHERE next_run_at IS NULL")
    op.alter_column("inspection_plans", "next_run_at", nullable=False)
    for column in [
        "data_source_id",
        "schedule_kind",
        "cron_expression",
        "schedule_timezone",
        "scheduled_at",
        "timeout_seconds",
        "max_retries",
    ]:
        op.drop_column("inspection_plans", column)
    op.drop_table("inspection_data_sources")
