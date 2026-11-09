from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class Application(TimestampMixin, Base):
    # Keep the public identity and SQLAlchemy write ordering.
    __module__ = "app.models"

    __tablename__ = "applications"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    code: Mapped[str] = mapped_column(String(100), unique=True)
    name: Mapped[str] = mapped_column(String(100))
    purpose: Mapped[str] = mapped_column(String(2000), default="", server_default="")
    depends_on: Mapped[list] = mapped_column(JSON, default=list, server_default="[]")
    owner: Mapped[str] = mapped_column(String(100), default="")
    entrypoint: Mapped[str] = mapped_column(String(500), default="")
    source_subdir: Mapped[str] = mapped_column(String(500), default=".")
    path_mappings: Mapped[list] = mapped_column(JSON, default=list)
    git_url: Mapped[str | None] = mapped_column(String(2000))
    git_ref: Mapped[str] = mapped_column(String(300), default="HEAD")
    git_username: Mapped[str] = mapped_column(String(200), default="")
    encrypted_git_token: Mapped[str | None] = mapped_column(Text)
    svn_url: Mapped[str | None] = mapped_column(String(2000))
    svn_revision: Mapped[str] = mapped_column(String(20), default="HEAD", server_default="HEAD")
    svn_username: Mapped[str] = mapped_column(String(200), default="", server_default="")
    encrypted_svn_password: Mapped[str | None] = mapped_column(Text)
    local_path: Mapped[str | None] = mapped_column(String(500))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    traffic_target: Mapped[str | None] = mapped_column(String(300))
    traffic_config_changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class SourceSnapshot(TimestampMixin, Base):
    # Keep the public identity and SQLAlchemy write ordering.
    __module__ = "app.models"

    __tablename__ = "source_snapshots"
    __table_args__ = (
        CheckConstraint(
            "status IN ('queued','running','ready','partial','failed','interrupted')",
            name="status",
        ),
        Index(
            "uq_source_snapshots_running_application",
            "application_id",
            unique=True,
            postgresql_where=text("status = 'running' AND deleted_at IS NULL"),
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    application_id: Mapped[UUID] = mapped_column(ForeignKey("applications.id"), index=True)
    requested_by: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    status: Mapped[str] = mapped_column(String(20), default="queued")
    source: Mapped[str | None] = mapped_column(String(20))
    request: Mapped[dict] = mapped_column(JSON)
    git_commit: Mapped[str | None] = mapped_column(String(64))
    svn_revision: Mapped[str | None] = mapped_column(String(20))
    content_hash: Mapped[str | None] = mapped_column(String(64))
    manifest: Mapped[list] = mapped_column(JSON, default=list)
    data_gaps: Mapped[list] = mapped_column(JSON, default=list)
    file_count: Mapped[int] = mapped_column(Integer, default=0)
    total_bytes: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(50))


class ApplicationRelease(TimestampMixin, Base):
    # Keep the public identity and SQLAlchemy write ordering.
    __module__ = "app.models"

    __tablename__ = "application_releases"
    __table_args__ = (UniqueConstraint("application_id", "version"),)

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    application_id: Mapped[UUID] = mapped_column(ForeignKey("applications.id"), index=True)
    version: Mapped[str] = mapped_column(String(100))
    snapshot_id: Mapped[UUID | None] = mapped_column(ForeignKey("source_snapshots.id"))
    expected_commit: Mapped[str | None] = mapped_column(String(64))
    expected_revision: Mapped[str | None] = mapped_column(String(20))
    image_ref: Mapped[str] = mapped_column(String(500), default="")
    image_id: Mapped[str | None] = mapped_column(String(71))
    repo_digest: Mapped[str | None] = mapped_column(String(1000))
    change_summary: Mapped[str] = mapped_column(Text, default="")
    test_result: Mapped[str] = mapped_column(Text, default="")
    build_source_status: Mapped[str] = mapped_column(String(30), default="declared")


class ApplicationDeployment(TimestampMixin, Base):
    # Keep the public identity and SQLAlchemy write ordering.
    __module__ = "app.models"

    __tablename__ = "application_deployments"
    __table_args__ = (
        CheckConstraint("num_nonnulls(container_id, llm_endpoint_id) = 1", name="one_target"),
        CheckConstraint("ended_at IS NULL OR ended_at > started_at", name="time_range"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    application_id: Mapped[UUID] = mapped_column(ForeignKey("applications.id"), index=True)
    release_id: Mapped[UUID] = mapped_column(ForeignKey("application_releases.id"), index=True)
    server_id: Mapped[UUID | None] = mapped_column(ForeignKey("servers.id"))
    container_id: Mapped[UUID | None] = mapped_column(ForeignKey("containers.id"), index=True)
    llm_endpoint_id: Mapped[UUID | None] = mapped_column(ForeignKey("llm_endpoints.id"), index=True)
    environment: Mapped[str] = mapped_column(String(50))
    compose_project: Mapped[str] = mapped_column(String(200), default="")
    compose_service: Mapped[str] = mapped_column(String(200), default="")
    config_version: Mapped[str] = mapped_column(String(200), default="")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    binding: Mapped[dict] = mapped_column(JSON)


class SourceAuditLog(TimestampMixin, Base):
    # Keep the public identity and SQLAlchemy write ordering.
    __module__ = "app.models"

    __tablename__ = "source_audit_logs"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    application_id: Mapped[UUID] = mapped_column(ForeignKey("applications.id"), index=True)
    user_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"))
    action: Mapped[str] = mapped_column(String(50))
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
