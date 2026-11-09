from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class Approval(TimestampMixin, Base):
    # Keep the public identity and SQLAlchemy write ordering.
    __module__ = "app.models"

    __tablename__ = "approvals"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending','approved','rejected','expired','invalidated','consumed')",
            name="status",
        ),
        Index(
            "uq_approvals_active_analysis",
            "analysis_id",
            unique=True,
            postgresql_where=text("status IN ('pending','approved') AND deleted_at IS NULL"),
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    incident_id: Mapped[UUID] = mapped_column(ForeignKey("incidents.id"), index=True)
    analysis_id: Mapped[UUID] = mapped_column(ForeignKey("agent_analyses.id"))
    container_id: Mapped[UUID] = mapped_column(ForeignKey("containers.id"))
    requested_by: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    approver_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"))
    status: Mapped[str] = mapped_column(String(20), default="pending")
    binding: Mapped[dict] = mapped_column(JSON)
    binding_hash: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decision_comment: Mapped[str | None] = mapped_column(String(2000))
    error_code: Mapped[str | None] = mapped_column(String(50))


class SkillExecution(TimestampMixin, Base):
    # Keep the public identity and SQLAlchemy write ordering.
    __module__ = "app.models"

    __tablename__ = "skill_executions"
    __table_args__ = (
        CheckConstraint(
            "status IN ('queued','prechecking','executing','verifying',"
            "'succeeded','failed','interrupted','unknown')",
            name="status",
        ),
        Index(
            "uq_skill_executions_active_container",
            "container_id",
            unique=True,
            postgresql_where=text(
                "status IN ('queued','prechecking','executing','verifying') AND deleted_at IS NULL"
            ),
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    approval_id: Mapped[UUID] = mapped_column(ForeignKey("approvals.id"), unique=True)
    incident_id: Mapped[UUID] = mapped_column(ForeignKey("incidents.id"), index=True)
    container_id: Mapped[UUID] = mapped_column(ForeignKey("containers.id"))
    initiated_by: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    status: Mapped[str] = mapped_column(String(20), default="queued")
    binding_hash: Mapped[str] = mapped_column(String(64))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    dispatched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    precheck: Mapped[dict] = mapped_column(JSON, default=dict)
    output: Mapped[dict] = mapped_column(JSON, default=dict)
    verification: Mapped[dict] = mapped_column(JSON, default=dict)
    error_code: Mapped[str | None] = mapped_column(String(50))


class AuditLog(TimestampMixin, Base):
    # Keep the public identity and SQLAlchemy write ordering.
    __module__ = "app.models"

    __tablename__ = "audit_logs"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    user_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"))
    incident_id: Mapped[UUID] = mapped_column(ForeignKey("incidents.id"), index=True)
    approval_id: Mapped[UUID | None] = mapped_column(ForeignKey("approvals.id"), index=True)
    report_id: Mapped[UUID | None] = mapped_column(ForeignKey("postmortem_reports.id"), index=True)
    execution_id: Mapped[UUID | None] = mapped_column(ForeignKey("skill_executions.id"))
    action: Mapped[str] = mapped_column(String(50))
    request_id: Mapped[str] = mapped_column(String(36))
    ip: Mapped[str | None] = mapped_column(String(100))
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
