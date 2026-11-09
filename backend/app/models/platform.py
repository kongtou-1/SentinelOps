from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import JSON, DateTime, Float, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class BackupRecord(TimestampMixin, Base):
    __module__ = "app.models"
    __tablename__ = "backup_records"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    kind: Mapped[str] = mapped_column(String(20))
    backup_id: Mapped[UUID | None] = mapped_column(ForeignKey("backup_records.id"))
    requested_by: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    idempotency_key: Mapped[UUID] = mapped_column(unique=True)
    request_hash: Mapped[str] = mapped_column(String(64))
    artifacts: Mapped[list] = mapped_column(JSON)
    completed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    duration_seconds: Mapped[float | None] = mapped_column(Float)
    verification: Mapped[str] = mapped_column(String(20))
    evidence: Mapped[str] = mapped_column(String(8000))
