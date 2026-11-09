"""Bind updated_at triggers for inspection and agent tables added in 0037-0043."""

from alembic import op

revision = "0047_inspection_agent_timestamps"
down_revision = "0046_askdata_definitions"
branch_labels = None
depends_on = None

_TABLES = (
    "inspection_traces",
    "inspection_http_connections",
    "agent_channels",
    "inspection_check_results",
    "agent_configs",
)


def upgrade():
    for table in _TABLES:
        op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_updated_at ON {table}")
        op.execute(
            f"CREATE TRIGGER trg_{table}_updated_at BEFORE UPDATE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION set_updated_at()"
        )


def downgrade():
    for table in _TABLES:
        op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_updated_at ON {table}")
