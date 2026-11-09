from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class MarketCategory(TimestampMixin, Base):
    # Keep the public identity and SQLAlchemy write ordering.
    __module__ = "app.models"

    __tablename__ = "market_categories"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(60))
    description: Mapped[str] = mapped_column(String(300), default="")
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    __table_args__ = (
        Index(
            "uq_market_category_name",
            "name",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )


class MarketEntry(TimestampMixin, Base):
    # Keep the public identity and SQLAlchemy write ordering.
    __module__ = "app.models"

    __tablename__ = "market_entries"
    __table_args__ = (CheckConstraint("kind IN ('skill','tool')", name="kind"),)

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    kind: Mapped[str] = mapped_column(String(10))
    owner_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), index=True)
    listed: Mapped[bool] = mapped_column(Boolean, default=False)
    published_revision_id: Mapped[UUID | None] = mapped_column(
        ForeignKey(
            "market_entry_revisions.id",
            use_alter=True,
            name="fk_market_entries_published_revision_id_market_entry_revisions",
        )
    )
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class MarketRevision(TimestampMixin, Base):
    # Keep the public identity and SQLAlchemy write ordering.
    __module__ = "app.models"

    __tablename__ = "market_entry_revisions"
    __table_args__ = (
        UniqueConstraint("entry_id", "number"),
        CheckConstraint("status IN ('draft','pending','approved','rejected')", name="status"),
        Index(
            "uq_market_revision_working",
            "entry_id",
            unique=True,
            postgresql_where=text("status IN ('draft','pending') AND deleted_at IS NULL"),
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    entry_id: Mapped[UUID] = mapped_column(ForeignKey("market_entries.id"), index=True)
    number: Mapped[int] = mapped_column(Integer, default=1)
    edit_version: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(20), default="draft")
    category_id: Mapped[UUID] = mapped_column(ForeignKey("market_categories.id"), index=True)
    title: Mapped[str] = mapped_column(String(100))
    summary: Mapped[str] = mapped_column(String(300))
    tags: Mapped[list] = mapped_column(JSON, default=list)
    content: Mapped[dict] = mapped_column(JSON, default=dict)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reviewer_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"))
    review_note: Mapped[str] = mapped_column(String(2000), default="")


class MarketAuditLog(TimestampMixin, Base):
    # Keep the public identity and SQLAlchemy write ordering.
    __module__ = "app.models"

    __tablename__ = "market_audit_logs"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    entry_id: Mapped[UUID | None] = mapped_column(ForeignKey("market_entries.id"), index=True)
    revision_id: Mapped[UUID | None] = mapped_column(ForeignKey("market_entry_revisions.id"))
    category_id: Mapped[UUID | None] = mapped_column(ForeignKey("market_categories.id"))
    action: Mapped[str] = mapped_column(String(50))
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
