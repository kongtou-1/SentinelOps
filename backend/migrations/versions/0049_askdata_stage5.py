"""Stage-5 governance records, saved analyses and per-connection execution settings.

Zones, tags, business rules, sensitive-field policies, supplementary definitions
and saved analyses are connection-scoped: a rebuild produces a new semantic
version but must not silently drop a human decision. Execution settings live on
``configurations``; the vector signature is pinned on each model version so a
changed embedding model cannot reuse old vectors.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0049_askdata_stage5"
down_revision = "0048_askdata_code_rules"
branch_labels = None
depends_on = None

DEFAULT_VECTOR_SIGNATURE = "BAAI/bge-small-zh-v1.5:512"

def _timestamps():
    return (
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
    )


def _trigger(table):
    op.execute(
        f"CREATE TRIGGER touch_updated_at BEFORE UPDATE ON askdata.{table} "
        "FOR EACH ROW EXECUTE FUNCTION askdata.touch_updated_at()"
    )


def upgrade():
    # Execution and model settings.
    op.add_column(
        "configurations",
        sa.Column("budget_seconds", sa.Integer(), nullable=False, server_default=sa.text("180")),
        schema="askdata",
    )
    op.add_column(
        "configurations", sa.Column("reasoning_channel_id", sa.Uuid()), schema="askdata"
    )
    op.add_column("configurations", sa.Column("light_channel_id", sa.Uuid()), schema="askdata")
    op.add_column(
        "configurations",
        sa.Column("vector_model", sa.String(120), nullable=False, server_default=sa.text("''")),
        schema="askdata",
    )
    op.add_column(
        "configurations",
        sa.Column(
            "vector_dimension", sa.Integer(), nullable=False, server_default=sa.text("512")
        ),
        schema="askdata",
    )
    op.add_column(
        "configurations",
        sa.Column(
            "reranker_enabled", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        schema="askdata",
    )
    op.add_column(
        "configurations",
        sa.Column("reranker_endpoint", sa.String(300), nullable=False, server_default=sa.text("''")),
        schema="askdata",
    )
    op.add_column(
        "configurations",
        sa.Column("reranker_model", sa.String(120), nullable=False, server_default=sa.text("''")),
        schema="askdata",
    )
    op.add_column("configurations", sa.Column("reranker_key_encrypted", sa.Text()), schema="askdata")
    op.add_column(
        "configurations", sa.Column("reranker_verified_at", sa.DateTime(timezone=True)), schema="askdata"
    )
    op.add_column("configurations", sa.Column("updated_by", sa.Uuid()), schema="askdata")
    op.create_foreign_key(
        "fk_configurations_reasoning_channel_id_agent_channels",
        "configurations",
        "agent_channels",
        ["reasoning_channel_id"],
        ["id"],
        source_schema="askdata",
    )
    op.create_foreign_key(
        "fk_configurations_light_channel_id_agent_channels",
        "configurations",
        "agent_channels",
        ["light_channel_id"],
        ["id"],
        source_schema="askdata",
    )
    op.create_foreign_key(
        "fk_configurations_updated_by_users",
        "configurations",
        "users",
        ["updated_by"],
        ["id"],
        source_schema="askdata",
    )
    op.add_column(
        "model_versions",
        sa.Column(
            "vector_signature", sa.String(140), nullable=False, server_default=sa.text("''")
        ),
        schema="askdata",
    )
    # Existing versions were built with the platform's local embedding model.
    op.execute(
        "UPDATE askdata.model_versions SET vector_signature = "
        f"'{DEFAULT_VECTOR_SIGNATURE}' WHERE vector_signature = ''"
    )

    op.create_table(
        "zones",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("connection_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("description", sa.String(300), nullable=False, server_default=sa.text("''")),
        sa.Column(
            "targets", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")
        ),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("updated_by", sa.Uuid()),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["connection_id"],
            ["service_databases.id"],
            name="fk_zones_connection_id_service_databases",
        ),
        sa.ForeignKeyConstraint(["updated_by"], ["users.id"], name="fk_zones_updated_by_users"),
        sa.PrimaryKeyConstraint("id", name="pk_zones"),
        sa.UniqueConstraint("connection_id", "name", name="uq_askdata_zones_name"),
        schema="askdata",
    )
    op.create_index("ix_zones_connection_id", "zones", ["connection_id"], schema="askdata")
    _trigger("zones")

    op.create_table(
        "tags",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("connection_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(60), nullable=False),
        sa.Column("color", sa.String(20), nullable=False, server_default=sa.text("''")),
        sa.Column("description", sa.String(300), nullable=False, server_default=sa.text("''")),
        sa.Column("updated_by", sa.Uuid()),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["connection_id"],
            ["service_databases.id"],
            name="fk_tags_connection_id_service_databases",
        ),
        sa.ForeignKeyConstraint(["updated_by"], ["users.id"], name="fk_tags_updated_by_users"),
        sa.PrimaryKeyConstraint("id", name="pk_tags"),
        sa.UniqueConstraint("connection_id", "name", name="uq_askdata_tags_name"),
        schema="askdata",
    )
    op.create_index("ix_tags_connection_id", "tags", ["connection_id"], schema="askdata")
    _trigger("tags")

    op.create_table(
        "tag_targets",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("connection_id", sa.Uuid(), nullable=False),
        sa.Column("tag_id", sa.Uuid(), nullable=False),
        sa.Column("object_kind", sa.String(20), nullable=False),
        sa.Column("object_key", sa.String(300), nullable=False),
        *_timestamps(),
        sa.CheckConstraint(
            "object_kind IN ('table','column','term','metric','code_rule')",
            name="ck_askdata_tag_targets_kind",
        ),
        sa.ForeignKeyConstraint(
            ["connection_id"],
            ["service_databases.id"],
            name="fk_tag_targets_connection_id_service_databases",
        ),
        sa.ForeignKeyConstraint(
            ["tag_id"],
            ["askdata.tags.id"],
            name="fk_tag_targets_tag_id_tags",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_tag_targets"),
        sa.UniqueConstraint(
            "tag_id", "object_kind", "object_key", name="uq_askdata_tag_targets_object"
        ),
        schema="askdata",
    )
    op.create_index(
        "ix_tag_targets_connection_id", "tag_targets", ["connection_id"], schema="askdata"
    )
    op.create_index("ix_tag_targets_tag_id", "tag_targets", ["tag_id"], schema="askdata")
    _trigger("tag_targets")

    op.create_table(
        "rules",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("connection_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("description", sa.String(300), nullable=False, server_default=sa.text("''")),
        sa.Column("kind", sa.String(30), nullable=False),
        sa.Column("target", sa.String(300), nullable=False, server_default=sa.text("''")),
        sa.Column("expression", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("unit", sa.String(30), nullable=False, server_default=sa.text("''")),
        sa.Column("priority", sa.Integer(), nullable=False, server_default=sa.text("100")),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("updated_by", sa.Uuid()),
        *_timestamps(),
        sa.CheckConstraint(
            "kind IN ('statistical_scope','time_semantics','unit','filter','soft_delete','status')",
            name="ck_askdata_rules_kind",
        ),
        sa.ForeignKeyConstraint(
            ["connection_id"],
            ["service_databases.id"],
            name="fk_rules_connection_id_service_databases",
        ),
        sa.ForeignKeyConstraint(["updated_by"], ["users.id"], name="fk_rules_updated_by_users"),
        sa.PrimaryKeyConstraint("id", name="pk_rules"),
        sa.UniqueConstraint("connection_id", "name", name="uq_askdata_rules_name"),
        schema="askdata",
    )
    op.create_index("ix_rules_connection_id", "rules", ["connection_id"], schema="askdata")
    _trigger("rules")

    op.create_table(
        "sensitive_fields",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("connection_id", sa.Uuid(), nullable=False),
        sa.Column("object_key", sa.String(300), nullable=False),
        sa.Column("category", sa.String(20), nullable=False, server_default=sa.text("'other'")),
        sa.Column("policy", sa.String(12), nullable=False, server_default=sa.text("'mask'")),
        sa.Column(
            "mask_strategy", sa.String(20), nullable=False, server_default=sa.text("'full'")
        ),
        sa.Column("mask_argument", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("sampling", sa.String(12), nullable=False, server_default=sa.text("'exclude'")),
        sa.Column("note", sa.String(300), nullable=False, server_default=sa.text("''")),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("updated_by", sa.Uuid()),
        *_timestamps(),
        sa.CheckConstraint(
            "category IN ('mobile','id_card','bank_card','email','address','name',"
            "'amount','credential','other')",
            name="ck_askdata_sensitive_fields_category",
        ),
        sa.CheckConstraint(
            "policy IN ('mask','exclude')", name="ck_askdata_sensitive_fields_policy"
        ),
        sa.CheckConstraint(
            "mask_strategy IN ('keep_prefix','keep_suffix','hash','full')",
            name="ck_askdata_sensitive_fields_mask",
        ),
        sa.CheckConstraint(
            "sampling IN ('allow','exclude')", name="ck_askdata_sensitive_fields_sampling"
        ),
        sa.ForeignKeyConstraint(
            ["connection_id"],
            ["service_databases.id"],
            name="fk_sensitive_fields_connection_id_service_databases",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by"], ["users.id"], name="fk_sensitive_fields_updated_by_users"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_sensitive_fields"),
        sa.UniqueConstraint(
            "connection_id", "object_key", name="uq_askdata_sensitive_fields_object"
        ),
        schema="askdata",
    )
    op.create_index(
        "ix_sensitive_fields_connection_id",
        "sensitive_fields",
        ["connection_id"],
        schema="askdata",
    )
    _trigger("sensitive_fields")

    op.create_table(
        "supplements",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("connection_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False, server_default=sa.text("'alias'")),
        sa.Column("term", sa.String(120), nullable=False),
        sa.Column("definition", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column(
            "aliases", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")
        ),
        sa.Column("applies_to", sa.String(300), nullable=False, server_default=sa.text("''")),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("updated_by", sa.Uuid()),
        *_timestamps(),
        sa.CheckConstraint(
            "kind IN ('abbreviation','alias','context')", name="ck_askdata_supplements_kind"
        ),
        sa.ForeignKeyConstraint(
            ["connection_id"],
            ["service_databases.id"],
            name="fk_supplements_connection_id_service_databases",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by"], ["users.id"], name="fk_supplements_updated_by_users"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_supplements"),
        sa.UniqueConstraint("connection_id", "term", name="uq_askdata_supplements_term"),
        schema="askdata",
    )
    op.create_index(
        "ix_supplements_connection_id", "supplements", ["connection_id"], schema="askdata"
    )
    _trigger("supplements")

    op.create_table(
        "analyses",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("connection_id", sa.Uuid(), nullable=False),
        sa.Column("model_version_id", sa.Uuid()),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("category", sa.String(60), nullable=False, server_default=sa.text("''")),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("sql_text", sa.Text(), nullable=False),
        sa.Column("explanation", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column(
            "chart", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column(
            "evidence", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")
        ),
        sa.Column("source_run_id", sa.Uuid()),
        sa.Column("last_status", sa.String(20), nullable=False, server_default=sa.text("''")),
        sa.Column("last_error_code", sa.String(80)),
        sa.Column("last_row_count", sa.Integer()),
        sa.Column("last_duration_ms", sa.Integer()),
        sa.Column("last_executed_at", sa.DateTime(timezone=True)),
        *_timestamps(),
        sa.CheckConstraint(
            "last_status IN ('completed','failed','cancelled','')",
            name="ck_askdata_analyses_last_status",
        ),
        sa.ForeignKeyConstraint(
            ["connection_id"],
            ["service_databases.id"],
            name="fk_analyses_connection_id_service_databases",
        ),
        sa.ForeignKeyConstraint(
            ["model_version_id"],
            ["askdata.model_versions.id"],
            name="fk_analyses_model_version_id_model_versions",
        ),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], name="fk_analyses_owner_id_users"),
        sa.ForeignKeyConstraint(
            ["source_run_id"], ["askdata.runs.id"], name="fk_analyses_source_run_id_runs"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_analyses"),
        sa.UniqueConstraint(
            "connection_id", "owner_id", "name", name="uq_askdata_analyses_name"
        ),
        schema="askdata",
    )
    op.create_index("ix_analyses_connection_id", "analyses", ["connection_id"], schema="askdata")
    op.create_index("ix_analyses_owner_id", "analyses", ["owner_id"], schema="askdata")
    op.create_index(
        "ix_askdata_analyses_owner", "analyses", ["connection_id", "owner_id"], schema="askdata"
    )
    _trigger("analyses")

    op.create_table(
        "imports",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("connection_id", sa.Uuid(), nullable=False),
        sa.Column("model_version_id", sa.Uuid()),
        sa.Column("requested_by", sa.Uuid(), nullable=False),
        sa.Column("filename", sa.String(200), nullable=False, server_default=sa.text("''")),
        sa.Column("status", sa.String(20), nullable=False, server_default=sa.text("'previewed'")),
        sa.Column(
            "conflict_policy", sa.String(20), nullable=False, server_default=sa.text("'skip'")
        ),
        sa.Column(
            "document", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column(
            "report", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column("applied_at", sa.DateTime(timezone=True)),
        *_timestamps(),
        sa.CheckConstraint(
            "status IN ('previewed','applied','failed')", name="ck_askdata_imports_status"
        ),
        sa.ForeignKeyConstraint(
            ["connection_id"],
            ["service_databases.id"],
            name="fk_imports_connection_id_service_databases",
        ),
        sa.ForeignKeyConstraint(
            ["model_version_id"],
            ["askdata.model_versions.id"],
            name="fk_imports_model_version_id_model_versions",
        ),
        sa.ForeignKeyConstraint(
            ["requested_by"], ["users.id"], name="fk_imports_requested_by_users"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_imports"),
        schema="askdata",
    )
    op.create_index("ix_imports_connection_id", "imports", ["connection_id"], schema="askdata")
    _trigger("imports")


def downgrade():
    bind = op.get_bind()
    for table in (
        "imports",
        "analyses",
        "supplements",
        "sensitive_fields",
        "rules",
        "tag_targets",
        "tags",
        "zones",
    ):
        if bind.scalar(sa.text(f'SELECT EXISTS(SELECT 1 FROM askdata."{table}")')):
            raise RuntimeError(f"Ask-data {table} exist; preserve them before rollback")
    for table in (
        "imports",
        "analyses",
        "supplements",
        "sensitive_fields",
        "rules",
        "tag_targets",
        "tags",
        "zones",
    ):
        op.execute(f"DROP TRIGGER IF EXISTS touch_updated_at ON askdata.{table}")
        op.drop_table(table, schema="askdata")
    op.drop_column("model_versions", "vector_signature", schema="askdata")
    for name in (
        "fk_configurations_updated_by_users",
        "fk_configurations_light_channel_id_agent_channels",
        "fk_configurations_reasoning_channel_id_agent_channels",
    ):
        op.drop_constraint(name, "configurations", schema="askdata", type_="foreignkey")
    for column in (
        "updated_by",
        "reranker_verified_at",
        "reranker_key_encrypted",
        "reranker_model",
        "reranker_endpoint",
        "reranker_enabled",
        "vector_dimension",
        "vector_model",
        "light_channel_id",
        "reasoning_channel_id",
        "budget_seconds",
    ):
        op.drop_column("configurations", column, schema="askdata")
