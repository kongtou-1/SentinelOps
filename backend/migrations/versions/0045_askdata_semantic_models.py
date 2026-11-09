"""Versioned semantic models, build progress and platform vector rows."""

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

revision = "0045_askdata_semantic_models"
down_revision = "0044_askdata_foundation"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "model_versions", sa.Column("published_at", sa.DateTime(timezone=True)), schema="askdata"
    )
    op.add_column(
        "configurations",
        sa.Column("active_model_version_id", sa.Uuid(), nullable=True),
        schema="askdata",
    )
    op.create_foreign_key(
        "fk_configurations_active_model_version_id_model_versions",
        "configurations",
        "model_versions",
        ["active_model_version_id"],
        ["id"],
        source_schema="askdata",
        referent_schema="askdata",
    )
    op.add_column(
        "builds",
        sa.Column(
            "detail",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'{}'::json"),
        ),
        schema="askdata",
    )
    op.add_column(
        "builds",
        sa.Column(
            "cancel_requested", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        schema="askdata",
    )
    op.add_column(
        "builds",
        sa.Column("started_at", sa.DateTime(timezone=True)),
        schema="askdata",
    )
    op.add_column(
        "builds",
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        schema="askdata",
    )
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.create_table(
        "embeddings",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("model_version_id", sa.Uuid(), nullable=False),
        sa.Column("collection", sa.String(20), nullable=False),
        sa.Column("path", sa.String(500), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("meta", postgresql.JSONB(), nullable=False),
        sa.Column("embedding", Vector(512), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "collection IN ('semantic','data')", name="ck_embeddings_collection"
        ),
        sa.ForeignKeyConstraint(
            ["model_version_id"],
            ["askdata.model_versions.id"],
            name="fk_embeddings_model_version_id_model_versions",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_embeddings"),
        sa.UniqueConstraint(
            "model_version_id", "collection", "path", name="uq_embeddings_model_version_id"
        ),
        schema="askdata",
    )
    op.create_index(
        "ix_embeddings_model_version_id",
        "embeddings",
        ["model_version_id"],
        schema="askdata",
    )
    op.execute(
        "CREATE INDEX ix_askdata_embeddings_vector ON askdata.embeddings "
        "USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64)"
    )
    op.execute(
        "CREATE TRIGGER touch_updated_at BEFORE UPDATE ON askdata.embeddings "
        "FOR EACH ROW EXECUTE FUNCTION askdata.touch_updated_at()"
    )


def downgrade():
    bind = op.get_bind()
    for name in ("embeddings", "builds", "model_versions"):
        if bind.scalar(sa.text(f'SELECT EXISTS(SELECT 1 FROM askdata."{name}")')):
            raise RuntimeError("Ask-data semantic records exist; preserve them before rollback")
    op.execute("DROP TRIGGER IF EXISTS touch_updated_at ON askdata.embeddings")
    op.drop_index("ix_askdata_embeddings_vector", table_name="embeddings", schema="askdata")
    op.drop_index("ix_embeddings_model_version_id", table_name="embeddings", schema="askdata")
    op.drop_table("embeddings", schema="askdata")
    op.drop_column("builds", "finished_at", schema="askdata")
    op.drop_column("builds", "started_at", schema="askdata")
    op.drop_column("builds", "cancel_requested", schema="askdata")
    op.drop_column("builds", "detail", schema="askdata")
    op.drop_constraint(
        "fk_configurations_active_model_version_id_model_versions",
        "configurations",
        type_="foreignkey",
        schema="askdata",
    )
    op.drop_column("configurations", "active_model_version_id", schema="askdata")
    op.drop_column("model_versions", "published_at", schema="askdata")
