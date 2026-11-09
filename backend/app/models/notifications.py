from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class NotificationPolicy(TimestampMixin, Base):
    __module__ = "app.models"
    __tablename__ = "notification_policy"

    id: Mapped[int] = mapped_column(primary_key=True, default=1)
    managed: Mapped[bool] = mapped_column(Boolean, default=False)
    email_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    configured_by: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    version: Mapped[int] = mapped_column(Integer, default=1)
    encrypted_smtp_config: Mapped[str | None] = mapped_column(Text)
    smtp_version: Mapped[int] = mapped_column(Integer, default=0, server_default="0")


class IncidentResponseRecord(TimestampMixin, Base):
    __module__ = "app.models"
    __tablename__ = "incident_responses"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    incident_id: Mapped[UUID] = mapped_column(ForeignKey("incidents.id"), unique=True)
    snapshot: Mapped[dict] = mapped_column(JSON)
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    accepted_by: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"))
    escalated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Notification(TimestampMixin, Base):
    __module__ = "app.models"
    __tablename__ = "notifications"
    __table_args__ = (
        CheckConstraint(
            "mail_status IN ('disabled','queued','sending','sent','failed','unknown')",
            name="mail_status",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), index=True)
    incident_id: Mapped[UUID | None] = mapped_column(ForeignKey("incidents.id"), index=True)
    kind: Mapped[str] = mapped_column(String(40))
    resource_kind: Mapped[str] = mapped_column(String(40))
    resource_id: Mapped[UUID] = mapped_column()
    dedupe_key: Mapped[str] = mapped_column(String(250), unique=True)
    title: Mapped[str] = mapped_column(String(200))
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    email: Mapped[str] = mapped_column(String(254), default="")
    policy_version: Mapped[int | None] = mapped_column(Integer)
    mail_status: Mapped[str] = mapped_column(String(20), default="disabled")
    dispatched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(80))


class MaintenanceWindow(TimestampMixin, Base):
    __module__ = "app.models"
    __tablename__ = "maintenance_windows"
    __table_args__ = (
        CheckConstraint("ends_at > starts_at", name="time_range"),
        CheckConstraint("scope_kind IN ('service','server','rule')", name="scope"),
        CheckConstraint("kind IN ('maintenance','silence')", name="kind"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    scope_kind: Mapped[str] = mapped_column(String(20))
    scope_id: Mapped[UUID] = mapped_column(index=True)
    kind: Mapped[str] = mapped_column(String(20))
    reason: Mapped[str] = mapped_column(String(2000))
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    version: Mapped[int] = mapped_column(Integer, default=1)


class AlertInhibition(TimestampMixin, Base):
    __module__ = "app.models"
    __tablename__ = "alert_inhibitions"
    __table_args__ = (
        UniqueConstraint("parent_rule_id", "child_rule_id"),
        CheckConstraint("parent_rule_id <> child_rule_id", name="different_rules"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    parent_rule_id: Mapped[UUID] = mapped_column(ForeignKey("alert_rules.id"))
    child_rule_id: Mapped[UUID] = mapped_column(ForeignKey("alert_rules.id"))
    created_by: Mapped[UUID] = mapped_column(ForeignKey("users.id"))


class OperationsAuditLog(TimestampMixin, Base):
    __module__ = "app.models"
    __tablename__ = "operations_audit_logs"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    user_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"))
    action: Mapped[str] = mapped_column(String(60))
    resource_kind: Mapped[str] = mapped_column(String(40))
    resource_id: Mapped[UUID | None] = mapped_column()
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
