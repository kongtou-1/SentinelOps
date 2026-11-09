"""User-specific resource denials, preserving existing global restrictions."""

import sqlalchemy as sa
from alembic import op

revision = "0039_user_resource_policies"
down_revision = "0038_platform_mail"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("access_policies", sa.Column("user_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "fk_access_policies_user_id_users", "access_policies", "users", ["user_id"], ["id"]
    )
    op.create_index("ix_access_policies_user_id", "access_policies", ["user_id"])
    op.drop_constraint("uq_access_policies_resource_kind", "access_policies", type_="unique")
    op.create_unique_constraint(
        "uq_access_policies_resource_kind",
        "access_policies",
        ["resource_kind", "resource_id", "user_id"],
        postgresql_nulls_not_distinct=True,
    )


def downgrade():
    if op.get_bind().scalar(
        sa.text("SELECT EXISTS (SELECT 1 FROM access_policies WHERE user_id IS NOT NULL)")
    ):
        raise RuntimeError("User resource policies exist; preserve them before rollback")
    op.drop_constraint("uq_access_policies_resource_kind", "access_policies", type_="unique")
    op.create_unique_constraint(
        "uq_access_policies_resource_kind", "access_policies", ["resource_kind", "resource_id"]
    )
    op.drop_index("ix_access_policies_user_id", table_name="access_policies")
    op.drop_constraint("fk_access_policies_user_id_users", "access_policies", type_="foreignkey")
    op.drop_column("access_policies", "user_id")
