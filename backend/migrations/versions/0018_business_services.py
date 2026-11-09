"""Business service ownership, independent of version/source deployment records."""

import sqlalchemy as sa
from alembic import op

revision = "0018_business_services"
down_revision = "0017_container_services"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "applications", sa.Column("purpose", sa.String(2000), nullable=False, server_default="")
    )
    op.add_column(
        "applications",
        sa.Column("depends_on", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
    )
    op.add_column("containers", sa.Column("application_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "fk_containers_application_id_applications",
        "containers",
        "applications",
        ["application_id"],
        ["id"],
    )
    op.create_index("ix_containers_application_id", "containers", ["application_id"])
    # Only an unambiguous, currently active source deployment establishes ownership.
    # Preserve all old per-container notes and all deployment evidence.
    op.execute("""
        UPDATE containers c SET application_id = candidates.application_id,
                                updated_at = clock_timestamp()
        FROM (
            SELECT d.container_id, (array_agg(DISTINCT d.application_id))[1] AS application_id
            FROM application_deployments d
            JOIN applications a ON a.id = d.application_id AND a.deleted_at IS NULL
            WHERE d.deleted_at IS NULL AND d.started_at <= now()
              AND (d.ended_at IS NULL OR d.ended_at > now())
              AND d.container_id IS NOT NULL
            GROUP BY d.container_id HAVING count(DISTINCT d.application_id) = 1
        ) candidates
        WHERE c.id = candidates.container_id AND c.deleted_at IS NULL
    """)


def downgrade():
    op.drop_index("ix_containers_application_id", table_name="containers")
    op.drop_constraint(
        "fk_containers_application_id_applications", "containers", type_="foreignkey"
    )
    op.drop_column("containers", "application_id")
    op.drop_column("applications", "depends_on")
    op.drop_column("applications", "purpose")
