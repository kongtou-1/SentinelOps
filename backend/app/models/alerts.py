from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.incidents import IncidentAlert


class AlertRule(TimestampMixin, Base):
    # Keep the public identity and SQLAlchemy write ordering.
    __module__ = "app.models"

    __tablename__ = "alert_rules"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    code: Mapped[str] = mapped_column(String(100), unique=True)
    service_monitor_id: Mapped[UUID | None] = mapped_column(ForeignKey("service_monitors.id"))
    server_id: Mapped[UUID | None] = mapped_column(ForeignKey("servers.id"))
    llm_endpoint_id: Mapped[UUID | None] = mapped_column(ForeignKey("llm_endpoints.id"))
    prediction_task_id: Mapped[UUID | None] = mapped_column(ForeignKey("prediction_tasks.id"))
    prediction_horizon_hours: Mapped[int | None] = mapped_column(Integer)
    metric: Mapped[str] = mapped_column(String(50))
    threshold: Mapped[float] = mapped_column(Float)
    for_seconds: Mapped[int] = mapped_column(Integer, default=30)
    severity: Mapped[str] = mapped_column(String(20), default="warning")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    auto_diagnose: Mapped[bool | None] = mapped_column(Boolean)
    auto_diagnose_by: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"))


class AlertEvent(TimestampMixin, Base):
    # Keep the public identity and SQLAlchemy write ordering.
    __module__ = "app.models"

    __tablename__ = "alert_events"
    __table_args__ = (
        UniqueConstraint("fingerprint", "starts_at"),
        Index(
            "ix_alert_events_recovery_pending",
            "recovery_checked_at",
            postgresql_where=text(
                "status = 'resolved' AND recovery_status = 'pending' AND deleted_at IS NULL"
            ),
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    fingerprint: Mapped[str] = mapped_column(String(64))
    rule_id: Mapped[UUID | None] = mapped_column(ForeignKey("alert_rules.id"))
    service_monitor_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("service_monitors.id"), index=True
    )
    server_id: Mapped[UUID | None] = mapped_column(ForeignKey("servers.id"), index=True)
    llm_endpoint_id: Mapped[UUID | None] = mapped_column(ForeignKey("llm_endpoints.id"), index=True)
    prediction_task_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("prediction_tasks.id"), index=True
    )
    severity: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20))
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    labels: Mapped[dict] = mapped_column(JSON)
    annotations: Mapped[dict] = mapped_column(JSON)
    rule_snapshot: Mapped[dict] = mapped_column(JSON, default=dict, server_default="{}")
    recovery_status: Mapped[str] = mapped_column(
        String(20), default="pending", server_default="pending"
    )
    recovery_reason: Mapped[str | None] = mapped_column(String(100))
    recovery_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    recovery_evidence_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    stability_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    stability_last_evidence_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    acknowledged_by: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"))
    comment: Mapped[str | None] = mapped_column(String(2000))
    incident_link: Mapped["IncidentAlert | None"] = relationship(lazy="selectin")

    @property
    def incident_id(self) -> UUID | None:
        return self.incident_link.incident_id if self.incident_link else None
