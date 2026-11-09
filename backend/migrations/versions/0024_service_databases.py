"""Service-scoped readonly database connections and frozen diagnosis contexts."""

import sqlalchemy as sa
from alembic import op

revision = "0024_service_databases"
down_revision = "0023_svn_sources"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "service_databases",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("application_id", sa.Uuid(), sa.ForeignKey("applications.id"), nullable=False),
        *[
            sa.Column(name, sa.String(size), nullable=False)
            for name, size in (
                ("name", 100),
                ("engine", 20),
                ("host", 253),
                ("database", 128),
                ("username", 128),
                ("tls_mode", 20),
                ("environment", 50),
                ("purpose", 2000),
                ("schema_hash", 64),
                ("permission_status", 30),
            )
        ],
        sa.Column("port", sa.Integer(), nullable=False),
        sa.Column("encrypted_config", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        *[
            sa.Column(name, sa.JSON(), nullable=False)
            for name in ("table_notes", "allowed_tables", "schema_cache")
        ],
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("query_enabled", sa.Boolean(), nullable=False),
        sa.Column("config_version", sa.Integer(), nullable=False),
        sa.Column("schema_refreshed_at", sa.DateTime(timezone=True)),
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
    op.create_index("ix_service_databases_application_id", "service_databases", ["application_id"])
    op.execute(
        "CREATE TRIGGER trg_service_databases_updated_at BEFORE UPDATE ON service_databases "
        "FOR EACH ROW EXECUTE FUNCTION set_updated_at()"
    )
    for table in ("agent_analyses", "analysis_runs"):
        op.add_column(
            table, sa.Column("database_context", sa.JSON(), nullable=False, server_default="{}")
        )


def downgrade():
    for table in ("agent_analyses", "analysis_runs"):
        op.drop_column(table, "database_context")
    op.drop_table("service_databases")
