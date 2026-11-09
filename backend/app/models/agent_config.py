from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class AgentConfig(TimestampMixin, Base):
    __module__ = "app.models"
    __tablename__ = "agent_configs"

    module: Mapped[str] = mapped_column(String(20), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")


class AgentChannel(TimestampMixin, Base):
    __module__ = "app.models"
    __tablename__ = "agent_channels"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    module: Mapped[str] = mapped_column(ForeignKey("agent_configs.module"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    position: Mapped[int] = mapped_column(Integer)
    base_url: Mapped[str] = mapped_column(String(2000))
    model: Mapped[str] = mapped_column(String(100))
    encrypted_api_key: Mapped[str] = mapped_column(Text)
    timeout_seconds: Mapped[int] = mapped_column(Integer, default=60)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_success: Mapped[bool | None] = mapped_column(Boolean)
    last_error: Mapped[str | None] = mapped_column(String(60))
    last_latency_ms: Mapped[float | None] = mapped_column()
