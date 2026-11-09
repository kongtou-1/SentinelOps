"""Business use of LLM endpoints, separate from version deployment records."""

import sqlalchemy as sa
from alembic import op

revision = "0020_llm_service_bindings"
down_revision = "0019_discover_market"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "llm_service_bindings",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("application_id", sa.Uuid(), sa.ForeignKey("applications.id"), nullable=False),
        sa.Column("llm_endpoint_id", sa.Uuid(), sa.ForeignKey("llm_endpoints.id"), nullable=False),
        sa.Column("purpose", sa.String(2000), nullable=False, server_default=""),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("application_id", "llm_endpoint_id"),
    )
    op.create_index(
        "ix_llm_service_bindings_application_id", "llm_service_bindings", ["application_id"]
    )
    op.create_index(
        "ix_llm_service_bindings_llm_endpoint_id", "llm_service_bindings", ["llm_endpoint_id"]
    )


def downgrade():
    op.drop_table("llm_service_bindings")
