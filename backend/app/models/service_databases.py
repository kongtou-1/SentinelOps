from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class ServiceDatabase(TimestampMixin, Base):
    __module__ = "app.models"
    __tablename__ = "service_databases"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    application_id: Mapped[UUID | None] = mapped_column(ForeignKey("applications.id"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    engine: Mapped[str] = mapped_column(String(20))
    host: Mapped[str] = mapped_column(String(253))
    port: Mapped[int] = mapped_column(Integer)
    database: Mapped[str] = mapped_column(String(128))
    username: Mapped[str] = mapped_column(String(128))
    tls_mode: Mapped[str] = mapped_column(String(20), default="verify-full")
    encrypted_config: Mapped[str] = mapped_column(Text)
    environment: Mapped[str] = mapped_column(String(50))
    purpose: Mapped[str] = mapped_column(String(2000), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    table_notes: Mapped[dict] = mapped_column(JSON, default=dict)
    allowed_tables: Mapped[dict] = mapped_column(JSON, default=dict)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    query_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    config_version: Mapped[int] = mapped_column(Integer, default=1)
    schema_cache: Mapped[dict] = mapped_column(JSON, default=dict)
    schema_hash: Mapped[str] = mapped_column(String(64), default="")
    schema_refreshed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_test_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_test_success: Mapped[bool | None] = mapped_column(Boolean)
    last_error_code: Mapped[str | None] = mapped_column(String(80))
    permission_status: Mapped[str] = mapped_column(String(30), default="unverified")
