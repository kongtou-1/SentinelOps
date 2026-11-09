from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class ServiceMonitor(TimestampMixin, Base):
    __module__ = "app.models"
    __tablename__ = "service_monitors"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    application_id: Mapped[UUID] = mapped_column(ForeignKey("applications.id"), index=True)
    container_id: Mapped[UUID | None] = mapped_column(ForeignKey("containers.id"))
    probe_server_id: Mapped[UUID | None] = mapped_column(ForeignKey("servers.id"))
    name: Mapped[str] = mapped_column(String(100))
    protocol: Mapped[str] = mapped_column(String(10), default="http")
    target: Mapped[str] = mapped_column(String(2000))
    method: Mapped[str] = mapped_column(String(10), default="GET")
    route: Mapped[str] = mapped_column(String(200), default="")
    dns_server: Mapped[str] = mapped_column(String(300), default="")
    importance: Mapped[str] = mapped_column(String(20), default="critical")
    expected_status: Mapped[int] = mapped_column(Integer, default=200)
    assertions: Mapped[dict] = mapped_column(JSON, default=dict)
    encrypted_request: Mapped[str | None] = mapped_column(Text)
    interval_seconds: Mapped[int] = mapped_column(Integer, default=60)
    timeout_seconds: Mapped[int] = mapped_column(Integer, default=5)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    config_changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    last_probe_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_probe_success: Mapped[bool | None] = mapped_column(Boolean)
    last_error_code: Mapped[str | None] = mapped_column(String(50))
    last_http_status: Mapped[int | None] = mapped_column(Integer)
    last_latency_seconds: Mapped[float | None] = mapped_column(Float)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_failure_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_evidence: Mapped[dict] = mapped_column(JSON, default=dict)
