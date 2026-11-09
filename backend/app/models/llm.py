from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class LLMEndpoint(TimestampMixin, Base):
    # Keep the public identity and SQLAlchemy write ordering.
    __module__ = "app.models"

    __tablename__ = "llm_endpoints"
    __table_args__ = (
        CheckConstraint(
            "(encrypted_api_key IS NULL) <> (agent_channel_id IS NULL)",
            name="key_source",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    provider: Mapped[str] = mapped_column(String(20), default="deepseek")
    base_url: Mapped[str] = mapped_column(String(2000))
    model: Mapped[str] = mapped_column(String(100))
    encrypted_api_key: Mapped[str | None] = mapped_column(Text)
    agent_channel_id: Mapped[UUID | None] = mapped_column(ForeignKey("agent_channels.id"))
    probe_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    probe_interval_seconds: Mapped[int] = mapped_column(Integer, default=60)
    probe_config_changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    last_probe_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_probe_success: Mapped[bool | None] = mapped_column(Boolean)
    last_error_type: Mapped[str | None] = mapped_column(String(40))
    last_http_status: Mapped[int | None] = mapped_column(Integer)
    last_latency_ms: Mapped[float | None] = mapped_column(Float)


class LLMServiceBinding(TimestampMixin, Base):
    # Keep the public identity and SQLAlchemy write ordering.
    __module__ = "app.models"

    __tablename__ = "llm_service_bindings"
    __table_args__ = (UniqueConstraint("application_id", "llm_endpoint_id"),)

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    application_id: Mapped[UUID] = mapped_column(ForeignKey("applications.id"), index=True)
    llm_endpoint_id: Mapped[UUID] = mapped_column(ForeignKey("llm_endpoints.id"), index=True)
    purpose: Mapped[str] = mapped_column(String(2000), default="", server_default="")
