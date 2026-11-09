from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class RetentionPlan(TimestampMixin, Base):
    __module__ = "app.models"
    __tablename__ = "retention_plans"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    created_by: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    confirmed_by: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"))
    status: Mapped[str] = mapped_column(String(20), default="preview")
    candidates: Mapped[list] = mapped_column(JSON, default=list)
    result: Mapped[list] = mapped_column(JSON, default=list)
    version: Mapped[int] = mapped_column(Integer, default=1)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(80))


class ArchiveRecord(TimestampMixin, Base):
    __module__ = "app.models"
    __tablename__ = "archive_records"
    __table_args__ = (UniqueConstraint("resource_kind", "resource_id"),)

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    plan_id: Mapped[UUID] = mapped_column(ForeignKey("retention_plans.id"), index=True)
    resource_kind: Mapped[str] = mapped_column(String(30))
    resource_id: Mapped[UUID] = mapped_column(index=True)
    owner_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"))
    status: Mapped[str] = mapped_column(String(20), default="ready")
    sha256: Mapped[str] = mapped_column(String(64))
    archive_bytes: Mapped[int] = mapped_column(Integer)
    released_file_bytes: Mapped[int] = mapped_column(Integer, default=0)
    database_reusable_bytes: Mapped[int] = mapped_column(Integer, default=0)
    metadata_snapshot: Mapped[dict] = mapped_column(JSON, default=dict)


class ModelUsage(TimestampMixin, Base):
    __module__ = "app.models"
    __tablename__ = "model_usage"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    dedupe_key: Mapped[str] = mapped_column(String(250), unique=True)
    source: Mapped[str] = mapped_column(String(30))
    provider_kind: Mapped[str] = mapped_column(String(20))
    application_id: Mapped[UUID | None] = mapped_column(ForeignKey("applications.id"), index=True)
    resource_kind: Mapped[str] = mapped_column(String(30))
    resource_id: Mapped[UUID] = mapped_column(index=True)
    purpose: Mapped[str] = mapped_column(String(20))
    operation: Mapped[str] = mapped_column(String(100))
    model: Mapped[str] = mapped_column(String(100))
    agent_module: Mapped[str | None] = mapped_column(String(20), index=True)
    agent_channel_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("agent_channels.id"), index=True
    )
    config_version: Mapped[int | None] = mapped_column(Integer)
    request_index: Mapped[int | None] = mapped_column(Integer)
    attempt_index: Mapped[int | None] = mapped_column(Integer)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    calls: Mapped[float | None] = mapped_column(Float)
    input_tokens: Mapped[float | None] = mapped_column(Float)
    output_tokens: Mapped[float | None] = mapped_column(Float)
    unknown_calls: Mapped[float | None] = mapped_column(Float)
    duration_seconds: Mapped[float | None] = mapped_column(Float)
    ttft_seconds: Mapped[float | None] = mapped_column(Float)
    outcome: Mapped[str] = mapped_column(String(30), default="unknown")
    detail: Mapped[dict] = mapped_column(JSON, default=dict)


class UsageCursor(TimestampMixin, Base):
    __module__ = "app.models"
    __tablename__ = "usage_cursors"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    application_id: Mapped[UUID] = mapped_column(ForeignKey("applications.id"), unique=True)
    source_epoch: Mapped[str] = mapped_column(String(100))
    observed_until: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ModelPrice(TimestampMixin, Base):
    __module__ = "app.models"
    __tablename__ = "model_prices"
    __table_args__ = (
        UniqueConstraint(
            "model",
            "currency",
            "effective_at",
            "agent_channel_id",
            postgresql_nulls_not_distinct=True,
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    model: Mapped[str] = mapped_column(String(100))
    agent_channel_id: Mapped[UUID | None] = mapped_column(ForeignKey("agent_channels.id"))
    currency: Mapped[str] = mapped_column(String(3))
    effective_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    input_per_million: Mapped[Decimal] = mapped_column(Numeric(20, 8))
    output_per_million: Mapped[Decimal] = mapped_column(Numeric(20, 8))
    created_by: Mapped[UUID] = mapped_column(ForeignKey("users.id"))


class UsageBudget(TimestampMixin, Base):
    __module__ = "app.models"
    __tablename__ = "usage_budgets"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(100))
    currency: Mapped[str] = mapped_column(String(3))
    monthly_limit: Mapped[Decimal] = mapped_column(Numeric(20, 8))
    application_id: Mapped[UUID | None] = mapped_column(ForeignKey("applications.id"))
    purpose: Mapped[str | None] = mapped_column(String(20))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    configured_by: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
