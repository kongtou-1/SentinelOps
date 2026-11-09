"""Human service descriptions and same-server dependencies."""

import sqlalchemy as sa
from alembic import op

revision = "0017_container_services"
down_revision = "0016_incident_closure"
branch_labels = None
depends_on = None


def upgrade():
    # Existing containers retain their TimestampMixin fields and update behavior.
    op.add_column(
        "containers", sa.Column("service_name", sa.String(100), nullable=False, server_default="")
    )
    op.add_column(
        "containers", sa.Column("purpose", sa.String(2000), nullable=False, server_default="")
    )
    op.add_column(
        "containers",
        sa.Column("depends_on", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
    )


def downgrade():
    for column in ("depends_on", "purpose", "service_name"):
        op.drop_column("containers", column)
