"""0003_resources."""

import sqlalchemy as sa
from alembic import op

revision = "0003_resources"
down_revision = "0002_users_auth"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "ssh_credentials",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("auth_type", sa.String(length=20), nullable=False),
        sa.Column("encrypted_secret", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ssh_credentials")),
        sa.UniqueConstraint("name", name=op.f("uq_ssh_credentials_name")),
    )
    op.create_table(
        "servers",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("host", sa.String(length=253), nullable=False),
        sa.Column("ssh_port", sa.Integer(), nullable=False),
        sa.Column("ssh_user", sa.String(length=64), nullable=False),
        sa.Column("credential_id", sa.Uuid(), nullable=True),
        sa.Column("ssh_host_key", sa.Text(), nullable=True),
        sa.Column("environment", sa.String(length=50), nullable=False),
        sa.Column("node_exporter_target", sa.String(length=300), nullable=False),
        sa.Column("cadvisor_target", sa.String(length=300), nullable=False),
        sa.Column("health_url", sa.String(length=2000), nullable=True),
        sa.Column("monitoring_enabled", sa.Boolean(), nullable=False),
        sa.Column("last_ssh_test_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_ssh_test_status", sa.String(length=30), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["credential_id"],
            ["ssh_credentials.id"],
            name=op.f("fk_servers_credential_id_ssh_credentials"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_servers")),
        sa.UniqueConstraint("name", name=op.f("uq_servers_name")),
    )
    op.create_table(
        "containers",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("server_id", sa.Uuid(), nullable=False),
        sa.Column("docker_id", sa.String(length=300), nullable=False),
        sa.Column("name", sa.String(length=300), nullable=False),
        sa.Column("image", sa.String(length=500), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("labels", sa.JSON(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["server_id"], ["servers.id"], name=op.f("fk_containers_server_id_servers")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_containers")),
        sa.UniqueConstraint("server_id", "docker_id", name=op.f("uq_containers_server_id")),
    )
    op.create_index(op.f("ix_containers_server_id"), "containers", ["server_id"], unique=False)
    for table in ("ssh_credentials", "servers", "containers"):
        op.execute(
            f"CREATE TRIGGER trg_{table}_updated_at BEFORE UPDATE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION set_updated_at()"
        )


def downgrade():
    op.drop_table("containers")
    op.drop_table("servers")
    op.drop_table("ssh_credentials")
