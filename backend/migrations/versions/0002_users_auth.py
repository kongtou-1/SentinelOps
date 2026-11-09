"""Users, fixed roles and revocable authentication sessions."""

from uuid import UUID

import sqlalchemy as sa
from alembic import op

revision = "0002_users_auth"
down_revision = "0001_foundation"
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
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    ]


def upgrade():
    roles = op.create_table(
        "roles",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("code", sa.String(20), nullable=False),
        sa.Column("name", sa.String(50), nullable=False),
        sa.UniqueConstraint("code", name="uq_roles_code"),
        *timestamps(),
    )
    op.bulk_insert(
        roles,
        [
            {
                "id": UUID("00000000-0000-0000-0000-000000000001"),
                "code": "viewer",
                "name": "Viewer",
            },
            {
                "id": UUID("00000000-0000-0000-0000-000000000002"),
                "code": "operator",
                "name": "Operator",
            },
            {"id": UUID("00000000-0000-0000-0000-000000000003"), "code": "admin", "name": "Admin"},
        ],
    )
    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("username", sa.String(64), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("role_id", sa.Uuid(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["role_id"], ["roles.id"], name="fk_users_role_id_roles"),
        sa.UniqueConstraint("username", name="uq_users_username"),
        *timestamps(),
    )
    op.create_table(
        "auth_sessions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("refresh_jti_hash", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_auth_sessions_user_id_users"),
        *timestamps(),
    )
    op.create_index("ix_auth_sessions_user_id", "auth_sessions", ["user_id"])
    for table in ("roles", "users", "auth_sessions"):
        op.execute(f"""
            CREATE TRIGGER trg_{table}_updated_at
            BEFORE UPDATE ON {table}
            FOR EACH ROW EXECUTE FUNCTION set_updated_at()
        """)


def downgrade():
    op.drop_table("auth_sessions")
    op.drop_table("users")
    op.drop_table("roles")
