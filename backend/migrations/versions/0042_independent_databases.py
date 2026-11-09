"""Allow database connections without a business service."""

import sqlalchemy as sa
from alembic import op

revision = "0042_independent_databases"
down_revision = "0041_inspection_trials"
branch_labels = None
depends_on = None


def upgrade():
    op.alter_column("service_databases", "application_id", existing_type=sa.Uuid(), nullable=True)


def downgrade():
    # PostgreSQL rejects the downgrade while standalone connections exist; do not
    # delete connections or invent a service assignment to make it succeed.
    op.alter_column("service_databases", "application_id", existing_type=sa.Uuid(), nullable=False)
