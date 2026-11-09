"""Application/source snapshots, releases and historical deployments."""

import sqlalchemy as sa
from alembic import op

revision = "0013_application_sources"
down_revision = "0012_analysis_conversations"
branch_labels = None
depends_on = None


def timestamps():
    return [
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
    ]


def upgrade():
    op.add_column("containers", sa.Column("image_id", sa.String(71)))
    op.add_column(
        "containers",
        sa.Column("identity", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
    )
    op.add_column("containers", sa.Column("identity_observed_at", sa.DateTime(timezone=True)))
    op.create_table(
        "applications",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("code", sa.String(100), nullable=False, unique=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("owner", sa.String(100), nullable=False),
        sa.Column("entrypoint", sa.String(500), nullable=False),
        sa.Column("source_subdir", sa.String(500), nullable=False),
        sa.Column("path_mappings", sa.JSON(), nullable=False),
        sa.Column("git_url", sa.String(2000)),
        sa.Column("git_ref", sa.String(300), nullable=False),
        sa.Column("git_username", sa.String(200), nullable=False),
        sa.Column("encrypted_git_token", sa.Text()),
        sa.Column("local_path", sa.String(500)),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        *timestamps(),
    )
    op.create_table(
        "source_snapshots",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("application_id", sa.Uuid(), sa.ForeignKey("applications.id"), nullable=False),
        sa.Column("requested_by", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("source", sa.String(20)),
        sa.Column("request", sa.JSON(), nullable=False),
        sa.Column("git_commit", sa.String(64)),
        sa.Column("content_hash", sa.String(64)),
        sa.Column("manifest", sa.JSON(), nullable=False),
        sa.Column("data_gaps", sa.JSON(), nullable=False),
        sa.Column("file_count", sa.Integer(), nullable=False),
        sa.Column("total_bytes", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("error_code", sa.String(50)),
        sa.CheckConstraint(
            "status IN ('queued','running','ready','partial','failed','interrupted')",
            name="status",
        ),
        *timestamps(),
    )
    op.create_index("ix_source_snapshots_application_id", "source_snapshots", ["application_id"])
    op.create_index(
        "uq_source_snapshots_running_application",
        "source_snapshots",
        ["application_id"],
        unique=True,
        postgresql_where=sa.text("status = 'running' AND deleted_at IS NULL"),
    )
    op.create_table(
        "application_releases",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("application_id", sa.Uuid(), sa.ForeignKey("applications.id"), nullable=False),
        sa.Column("version", sa.String(100), nullable=False),
        sa.Column("snapshot_id", sa.Uuid(), sa.ForeignKey("source_snapshots.id")),
        sa.Column("expected_commit", sa.String(64)),
        sa.Column("image_ref", sa.String(500), nullable=False),
        sa.Column("image_id", sa.String(71)),
        sa.Column("repo_digest", sa.String(1000)),
        sa.Column("change_summary", sa.Text(), nullable=False),
        sa.Column("test_result", sa.Text(), nullable=False),
        sa.Column("build_source_status", sa.String(30), nullable=False),
        sa.UniqueConstraint("application_id", "version"),
        *timestamps(),
    )
    op.create_index(
        "ix_application_releases_application_id", "application_releases", ["application_id"]
    )
    op.create_table(
        "application_deployments",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("application_id", sa.Uuid(), sa.ForeignKey("applications.id"), nullable=False),
        sa.Column(
            "release_id", sa.Uuid(), sa.ForeignKey("application_releases.id"), nullable=False
        ),
        sa.Column("server_id", sa.Uuid(), sa.ForeignKey("servers.id")),
        sa.Column("container_id", sa.Uuid(), sa.ForeignKey("containers.id")),
        sa.Column("llm_endpoint_id", sa.Uuid(), sa.ForeignKey("llm_endpoints.id")),
        sa.Column("environment", sa.String(50), nullable=False),
        sa.Column("compose_project", sa.String(200), nullable=False),
        sa.Column("compose_service", sa.String(200), nullable=False),
        sa.Column("config_version", sa.String(200), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True)),
        sa.Column("binding", sa.JSON(), nullable=False),
        sa.CheckConstraint("num_nonnulls(container_id, llm_endpoint_id) = 1", name="one_target"),
        sa.CheckConstraint("ended_at IS NULL OR ended_at > started_at", name="time_range"),
        *timestamps(),
    )
    for column in ("application_id", "release_id", "container_id", "llm_endpoint_id"):
        op.create_index(f"ix_application_deployments_{column}", "application_deployments", [column])
    op.create_table(
        "source_audit_logs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("application_id", sa.Uuid(), sa.ForeignKey("applications.id"), nullable=False),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id")),
        sa.Column("action", sa.String(50), nullable=False),
        sa.Column("detail", sa.JSON(), nullable=False),
        *timestamps(),
    )
    op.create_index("ix_source_audit_logs_application_id", "source_audit_logs", ["application_id"])
    for table in (
        "applications",
        "source_snapshots",
        "application_releases",
        "application_deployments",
        "source_audit_logs",
    ):
        op.execute(
            f"CREATE TRIGGER trg_{table}_updated_at BEFORE UPDATE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION set_updated_at()"
        )


def downgrade():
    for table in (
        "source_audit_logs",
        "application_deployments",
        "application_releases",
        "source_snapshots",
        "applications",
    ):
        op.drop_table(table)
    for column in ("identity_observed_at", "identity", "image_id"):
        op.drop_column("containers", column)
