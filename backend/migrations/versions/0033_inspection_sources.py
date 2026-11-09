"""Independent inspection plans and explicit related read-only sources."""

import sqlalchemy as sa
from alembic import op

revision = "0033_inspection_sources"
down_revision = "0032_business_inspections"
branch_labels = None
depends_on = None


def upgrade():
    op.alter_column("inspection_plans", "application_id", nullable=True)
    op.add_column(
        "inspection_plans",
        sa.Column("related_sources", sa.JSON(), nullable=False, server_default="[]"),
    )
    op.add_column("inspection_plans", sa.Column("encrypted_related_headers", sa.Text()))
    op.create_index(
        "ix_inspection_items_object", "inspection_items", ["plan_id", "record_id", "created_at"]
    )


def downgrade():
    connection = op.get_bind()
    if connection.scalar(
        sa.text("SELECT count(*) FROM inspection_plans WHERE application_id IS NULL")
    ):
        raise RuntimeError("Independent inspection plans must be migrated before downgrade")
    op.drop_index("ix_inspection_items_object", table_name="inspection_items")
    op.drop_column("inspection_plans", "encrypted_related_headers")
    op.drop_column("inspection_plans", "related_sources")
    op.alter_column("inspection_plans", "application_id", nullable=False)
