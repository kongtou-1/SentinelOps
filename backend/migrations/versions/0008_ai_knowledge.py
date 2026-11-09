"""Knowledge, immutable evidence snapshots, and durable AI analysis queue."""

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector

revision = "0008_ai_knowledge"
down_revision = "0007_incidents"
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
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.create_table(
        "knowledge_documents",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("source", sa.String(500), nullable=False),
        sa.Column("tags", sa.JSON(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("index_status", sa.String(20), nullable=False),
        sa.Column("error_code", sa.String(50)),
        *timestamps(),
    )
    op.create_table(
        "knowledge_chunks",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "document_id", sa.Uuid(), sa.ForeignKey("knowledge_documents.id"), nullable=False
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("embedding", Vector(512), nullable=False),
        *timestamps(),
    )
    op.create_index("ix_knowledge_chunks_document_id", "knowledge_chunks", ["document_id"])
    op.create_table(
        "knowledge_skills",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("code", sa.String(100), nullable=False, unique=True),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("tags", sa.JSON(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        *timestamps(),
    )
    op.create_table(
        "agent_analyses",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("incident_id", sa.Uuid(), sa.ForeignKey("incidents.id"), nullable=False),
        sa.Column("requested_by", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("input_hash", sa.String(64), nullable=False),
        sa.Column("config", sa.JSON(), nullable=False),
        sa.Column("session_id", sa.String(100)),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("error_code", sa.String(50)),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("knowledge", sa.JSON(), nullable=False),
        sa.Column("tool_calls", sa.JSON(), nullable=False),
        sa.Column("result", sa.JSON(), nullable=False),
        sa.CheckConstraint(
            "status IN ('queued','running','completed','failed','interrupted')", name="status"
        ),
        *timestamps(),
    )
    op.create_index("ix_agent_analyses_incident_id", "agent_analyses", ["incident_id"])
    op.create_index(
        "uq_agent_analyses_active_incident",
        "agent_analyses",
        ["incident_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('queued', 'running') AND deleted_at IS NULL"),
    )
    for name in ("knowledge_documents", "knowledge_chunks", "knowledge_skills", "agent_analyses"):
        op.execute(
            f"CREATE TRIGGER trg_{name}_updated_at BEFORE UPDATE ON {name} "
            "FOR EACH ROW EXECUTE FUNCTION set_updated_at()"
        )


def downgrade():
    for name in ("agent_analyses", "knowledge_skills", "knowledge_chunks", "knowledge_documents"):
        op.drop_table(name)
    # Extension is shared infrastructure; do not remove unrelated vector users.
