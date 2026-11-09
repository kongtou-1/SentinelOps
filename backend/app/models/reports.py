from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class PostmortemReport(TimestampMixin, Base):
    # Keep the public identity and SQLAlchemy write ordering.
    __module__ = "app.models"

    __tablename__ = "postmortem_reports"
    __table_args__ = (
        CheckConstraint(
            "status IN ('queued','running','draft','approved','failed','interrupted')",
            name="status",
        ),
        Index(
            "uq_postmortem_reports_active_incident",
            "incident_id",
            unique=True,
            postgresql_where=text("status IN ('queued','running') AND deleted_at IS NULL"),
        ),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    incident_id: Mapped[UUID] = mapped_column(ForeignKey("incidents.id"), index=True)
    requested_by: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    status: Mapped[str] = mapped_column(String(20), default="queued")
    revision: Mapped[int] = mapped_column(Integer, default=0)
    snapshot: Mapped[dict] = mapped_column(JSON)
    snapshot_hash: Mapped[str] = mapped_column(String(64))
    config: Mapped[dict] = mapped_column(JSON)
    tool_calls: Mapped[list] = mapped_column(JSON, default=list)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(50))
    reviewed_by: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    review_comment: Mapped[str | None] = mapped_column(String(2000))


class PostmortemRevision(TimestampMixin, Base):
    # Keep the public identity and SQLAlchemy write ordering.
    __module__ = "app.models"

    __tablename__ = "postmortem_revisions"
    __table_args__ = (UniqueConstraint("report_id", "revision"),)
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    report_id: Mapped[UUID] = mapped_column(ForeignKey("postmortem_reports.id"), index=True)
    revision: Mapped[int] = mapped_column(Integer)
    author_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"))
    content: Mapped[dict] = mapped_column(JSON)
    content_hash: Mapped[str] = mapped_column(String(64))


class PostmortemDelivery(TimestampMixin, Base):
    # Keep the public identity and SQLAlchemy write ordering.
    __module__ = "app.models"

    __tablename__ = "postmortem_deliveries"
    __table_args__ = (
        CheckConstraint(
            "status IN ('queued','sending','sent','failed','interrupted','unknown')", name="status"
        ),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    report_id: Mapped[UUID] = mapped_column(ForeignKey("postmortem_reports.id"), index=True)
    revision: Mapped[int] = mapped_column(Integer)
    requested_by: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    idempotency_key: Mapped[UUID] = mapped_column(unique=True)
    request_hash: Mapped[str] = mapped_column(String(64))
    recipients: Mapped[list] = mapped_column(JSON)
    sender: Mapped[str] = mapped_column(String(254))
    subject: Mapped[str] = mapped_column(String(300))
    body: Mapped[str] = mapped_column(Text)
    comment: Mapped[str] = mapped_column(String(2000))
    attachment: Mapped[bytes] = mapped_column(LargeBinary)
    attachment_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(20), default="queued")
    dispatched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(50))
