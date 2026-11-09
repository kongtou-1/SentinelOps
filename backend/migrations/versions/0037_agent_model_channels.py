"""Independent Agent model channels and attempt-level accounting."""

import sqlalchemy as sa
from alembic import op

revision = "0037_agent_model_channels"
down_revision = "0036_inspection_agent_skills"
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
    table = op.create_table(
        "agent_configs",
        sa.Column("module", sa.String(20), primary_key=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        *timestamps(),
    )
    op.bulk_insert(
        table, [{"module": m} for m in ("diagnosis", "inspection", "report", "evaluation")]
    )
    op.create_table(
        "agent_channels",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("module", sa.String(20), sa.ForeignKey("agent_configs.module"), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("base_url", sa.String(2000), nullable=False),
        sa.Column("model", sa.String(100), nullable=False),
        sa.Column("encrypted_api_key", sa.Text(), nullable=False),
        sa.Column("timeout_seconds", sa.Integer(), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True)),
        sa.Column("last_success", sa.Boolean()),
        sa.Column("last_error", sa.String(60)),
        sa.Column("last_latency_ms", sa.Float()),
        *timestamps(),
    )
    op.create_index("ix_agent_channels_module", "agent_channels", ["module"])
    op.add_column("model_usage", sa.Column("agent_module", sa.String(20)))
    op.add_column("model_usage", sa.Column("agent_channel_id", sa.Uuid()))
    op.create_foreign_key(None, "model_usage", "agent_channels", ["agent_channel_id"], ["id"])
    for field in ("config_version", "request_index", "attempt_index"):
        op.add_column("model_usage", sa.Column(field, sa.Integer()))
    for field in ("agent_module", "agent_channel_id"):
        op.create_index(f"ix_model_usage_{field}", "model_usage", [field])
    op.add_column("model_prices", sa.Column("agent_channel_id", sa.Uuid()))
    op.create_foreign_key(None, "model_prices", "agent_channels", ["agent_channel_id"], ["id"])
    op.drop_constraint("uq_model_prices_model", "model_prices", type_="unique")
    op.create_unique_constraint(
        "uq_model_prices_model",
        "model_prices",
        ["model", "currency", "effective_at", "agent_channel_id"],
        postgresql_nulls_not_distinct=True,
    )


def downgrade():
    # Channel-specific prices cannot be mapped to a single generic price without data loss.
    if op.get_bind().scalar(
        sa.text("SELECT EXISTS (SELECT 1 FROM model_prices WHERE agent_channel_id IS NOT NULL)")
    ):
        raise RuntimeError("Channel-specific prices exist; restore a backup to roll back safely")
    op.drop_constraint("uq_model_prices_model", "model_prices", type_="unique")
    op.create_unique_constraint(
        "uq_model_prices_model", "model_prices", ["model", "currency", "effective_at"]
    )
    op.drop_column("model_prices", "agent_channel_id")
    for field in (
        "attempt_index",
        "request_index",
        "config_version",
        "agent_channel_id",
        "agent_module",
    ):
        op.drop_column("model_usage", field)
    op.drop_table("agent_channels")
    op.drop_table("agent_configs")
