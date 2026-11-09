"""Durable source-code evidence for ask-data business rules (stage 4).

Every extracted rule keeps the snapshot identity, repository path, line range
and snippet hash that produced it. Rules are scoped to one model version, so a
rebuild never mutates the evidence used by an already published version.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0048_askdata_code_rules"
down_revision = "0047_inspection_agent_timestamps"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "code_rules",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("connection_id", sa.Uuid(), nullable=False),
        sa.Column("model_version_id", sa.Uuid(), nullable=False),
        sa.Column("source_snapshot_id", sa.Uuid()),
        sa.Column("git_commit", sa.String(64)),
        sa.Column("svn_revision", sa.String(40)),
        sa.Column("content_hash", sa.String(64), nullable=False, server_default=sa.text("''")),
        sa.Column("language", sa.String(10), nullable=False),
        sa.Column("path", sa.String(500), nullable=False),
        sa.Column("start_line", sa.Integer(), nullable=False),
        sa.Column("end_line", sa.Integer(), nullable=False),
        sa.Column("snippet_hash", sa.String(64), nullable=False, server_default=sa.text("''")),
        sa.Column("rule_kind", sa.String(30), nullable=False),
        sa.Column("object_key", sa.String(300), nullable=False, server_default=sa.text("''")),
        sa.Column("expression", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("sql_text", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column(
            "detail",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("parse_state", sa.String(12), nullable=False, server_default=sa.text("'static'")),
        sa.Column("source", sa.String(12), nullable=False, server_default=sa.text("'auto'")),
        sa.Column("fingerprint", sa.String(64), nullable=False, server_default=sa.text("''")),
        sa.Column("certified", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("edited_by", sa.Uuid()),
        sa.Column("review_required", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("review_note", sa.String(300), nullable=False, server_default=sa.text("''")),
        sa.Column("conflict_with", sa.Uuid()),
        sa.Column(
            "conflict_state", sa.String(12), nullable=False, server_default=sa.text("'none'")
        ),
        sa.Column("superseded", sa.Boolean(), nullable=False, server_default=sa.text("false")),
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
            "language IN ('python','java','other')", name="ck_askdata_code_rules_language"
        ),
        sa.CheckConstraint(
            "parse_state IN ('static','dynamic')", name="ck_askdata_code_rules_parse_state"
        ),
        sa.CheckConstraint("source IN ('auto','manual')", name="ck_askdata_code_rules_source"),
        sa.CheckConstraint(
            "conflict_state IN ('none','pending','resolved')",
            name="ck_askdata_code_rules_conflict_state",
        ),
        sa.ForeignKeyConstraint(
            ["connection_id"],
            ["service_databases.id"],
            name="fk_code_rules_connection_id_service_databases",
        ),
        sa.ForeignKeyConstraint(
            ["model_version_id"],
            ["askdata.model_versions.id"],
            name="fk_code_rules_model_version_id_model_versions",
        ),
        sa.ForeignKeyConstraint(
            ["source_snapshot_id"],
            ["source_snapshots.id"],
            name="fk_code_rules_source_snapshot_id_source_snapshots",
        ),
        sa.ForeignKeyConstraint(["edited_by"], ["users.id"], name="fk_code_rules_edited_by_users"),
        sa.PrimaryKeyConstraint("id", name="pk_code_rules"),
        sa.UniqueConstraint(
            "model_version_id", "fingerprint", name="uq_askdata_code_rules_fingerprint"
        ),
        schema="askdata",
    )
    op.create_index(
        "ix_code_rules_connection_id", "code_rules", ["connection_id"], schema="askdata"
    )
    op.create_index(
        "ix_code_rules_model_version_id", "code_rules", ["model_version_id"], schema="askdata"
    )
    op.create_index(
        "ix_askdata_code_rules_review",
        "code_rules",
        ["model_version_id", "review_required"],
        schema="askdata",
    )
    op.create_index(
        "ix_askdata_code_rules_source",
        "code_rules",
        ["model_version_id", "path", "rule_kind", "object_key"],
        schema="askdata",
    )
    op.execute(
        "CREATE TRIGGER touch_updated_at BEFORE UPDATE ON askdata.code_rules "
        "FOR EACH ROW EXECUTE FUNCTION askdata.touch_updated_at()"
    )


def downgrade():
    bind = op.get_bind()
    if bind.scalar(sa.text('SELECT EXISTS(SELECT 1 FROM askdata."code_rules")')):
        raise RuntimeError("Ask-data code rules exist; preserve them before rollback")
    op.execute("DROP TRIGGER IF EXISTS touch_updated_at ON askdata.code_rules")
    op.drop_index("ix_askdata_code_rules_source", table_name="code_rules", schema="askdata")
    op.drop_index("ix_askdata_code_rules_review", table_name="code_rules", schema="askdata")
    op.drop_index("ix_code_rules_model_version_id", table_name="code_rules", schema="askdata")
    op.drop_index("ix_code_rules_connection_id", table_name="code_rules", schema="askdata")
    op.drop_table("code_rules", schema="askdata")
