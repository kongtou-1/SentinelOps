"""Durable business-model definitions carried across semantic rebuilds."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0046_askdata_definitions"
down_revision = "0045_askdata_semantic_models"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "definitions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("connection_id", sa.Uuid(), nullable=False),
        sa.Column("model_version_id", sa.Uuid(), nullable=False),
        sa.Column("object_kind", sa.String(20), nullable=False),
        sa.Column("object_key", sa.String(300), nullable=False),
        sa.Column("object_id", sa.String(100)),
        sa.Column(
            "payload",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("certified", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("edited_by", sa.Uuid()),
        sa.Column(
            "source_fingerprint",
            sa.String(200),
            nullable=False,
            server_default=sa.text("''"),
        ),
        sa.Column("review_required", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("review_note", sa.String(300), nullable=False, server_default=sa.text("''")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "object_kind IN ('term','attribute','metric')",
            name="ck_askdata_definitions_kind",
        ),
        sa.ForeignKeyConstraint(
            ["connection_id"], ["service_databases.id"],
            name="fk_definitions_connection_id_service_databases",
        ),
        sa.ForeignKeyConstraint(
            ["model_version_id"], ["askdata.model_versions.id"],
            name="fk_definitions_model_version_id_model_versions",
        ),
        sa.ForeignKeyConstraint(
            ["edited_by"], ["users.id"], name="fk_definitions_edited_by_users"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_definitions"),
        sa.UniqueConstraint(
            "model_version_id", "object_kind", "object_key",
            name="uq_askdata_definitions_object",
        ),
        schema="askdata",
    )
    op.create_index(
        "ix_definitions_connection_id", "definitions", ["connection_id"], schema="askdata"
    )
    op.create_index(
        "ix_definitions_model_version_id", "definitions", ["model_version_id"], schema="askdata"
    )
    op.create_index(
        "ix_askdata_definitions_review",
        "definitions",
        ["connection_id", "review_required"],
        schema="askdata",
    )
    op.execute(
        "CREATE TRIGGER touch_updated_at BEFORE UPDATE ON askdata.definitions "
        "FOR EACH ROW EXECUTE FUNCTION askdata.touch_updated_at()"
    )


def downgrade():
    bind = op.get_bind()
    if bind.scalar(sa.text('SELECT EXISTS(SELECT 1 FROM askdata."definitions")')):
        raise RuntimeError("Ask-data definitions exist; preserve them before rollback")
    op.execute("DROP TRIGGER IF EXISTS touch_updated_at ON askdata.definitions")
    op.drop_index("ix_askdata_definitions_review", table_name="definitions", schema="askdata")
    op.drop_index("ix_definitions_model_version_id", table_name="definitions", schema="askdata")
    op.drop_index("ix_definitions_connection_id", table_name="definitions", schema="askdata")
    op.drop_table("definitions", schema="askdata")
