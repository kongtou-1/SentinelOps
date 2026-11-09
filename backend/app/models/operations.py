from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class ServiceObjective(TimestampMixin, Base):
    __module__ = "app.models"
    __tablename__ = "service_objectives"
    __table_args__ = (
        CheckConstraint("window_days IN (1,7,30)", name="window"),
        CheckConstraint(
            "kind IN ('business_success','probe_availability','request_p95','llm_ttft_p95')",
            name="kind",
        ),
        CheckConstraint("target > 0", name="target"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    application_id: Mapped[UUID] = mapped_column(ForeignKey("applications.id"), index=True)
    monitor_id: Mapped[UUID | None] = mapped_column(ForeignKey("service_monitors.id"))
    name: Mapped[str] = mapped_column(String(100))
    kind: Mapped[str] = mapped_column(String(30))
    operation: Mapped[str] = mapped_column(String(100), default="")
    metric_source: Mapped[str] = mapped_column(
        String(20), default="application", server_default="application"
    )
    target: Mapped[float] = mapped_column(Float)
    window_days: Mapped[int] = mapped_column(Integer, default=7)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    evaluation: Mapped[dict] = mapped_column(JSON, default=dict, server_default="{}")
    checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class WorkerHeartbeat(TimestampMixin, Base):
    __module__ = "app.models"
    __tablename__ = "worker_heartbeats"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    state: Mapped[str] = mapped_column(String(30), default="idle")
    detail: Mapped[dict] = mapped_column(JSON, default=dict, server_default="{}")


class ServiceOperationsPolicy(TimestampMixin, Base):
    __module__ = "app.models"
    __tablename__ = "service_operations_policies"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    application_id: Mapped[UUID] = mapped_column(ForeignKey("applications.id"), unique=True)
    recovery_seconds: Mapped[int] = mapped_column(Integer, default=180)
    version: Mapped[int] = mapped_column(Integer, default=1)
    response_config: Mapped[dict] = mapped_column(JSON, default=dict, server_default="{}")


class IncidentRelation(TimestampMixin, Base):
    __module__ = "app.models"
    __tablename__ = "incident_relations"
    __table_args__ = (
        UniqueConstraint("incident_id", "related_incident_id", "kind"),
        CheckConstraint("status IN ('candidate','confirmed','dismissed')", name="status"),
        CheckConstraint("kind IN ('related','dependency','recurrence')", name="kind"),
        CheckConstraint("incident_id <> related_incident_id", name="different_incidents"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    incident_id: Mapped[UUID] = mapped_column(ForeignKey("incidents.id"), index=True)
    related_incident_id: Mapped[UUID] = mapped_column(ForeignKey("incidents.id"), index=True)
    kind: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20), default="candidate")
    evidence: Mapped[dict] = mapped_column(JSON, default=dict, server_default="{}")
    decided_by: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"))
    version: Mapped[int] = mapped_column(Integer, default=1)
