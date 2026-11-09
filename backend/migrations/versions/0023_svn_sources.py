"""SVN source connections and fixed revision snapshots."""

import sqlalchemy as sa
from alembic import op

revision = "0023_svn_sources"
down_revision = "0022_service_observability"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("applications", sa.Column("svn_url", sa.String(2000)))
    op.add_column(
        "applications",
        sa.Column("svn_revision", sa.String(20), nullable=False, server_default="HEAD"),
    )
    op.add_column(
        "applications", sa.Column("svn_username", sa.String(200), nullable=False, server_default="")
    )
    op.add_column("applications", sa.Column("encrypted_svn_password", sa.Text()))
    op.add_column("source_snapshots", sa.Column("svn_revision", sa.String(20)))
    op.add_column("application_releases", sa.Column("expected_revision", sa.String(20)))


def downgrade():
    op.drop_column("application_releases", "expected_revision")
    op.drop_column("source_snapshots", "svn_revision")
    for name in ("encrypted_svn_password", "svn_username", "svn_revision", "svn_url"):
        op.drop_column("applications", name)
