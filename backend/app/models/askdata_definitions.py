"""Human-authored business definitions that survive automatic rebuilds."""

from uuid import UUID, uuid4

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class AskDataDefinition(TimestampMixin, Base):
    """Certified or manually revised term / attribute / metric definitions.

    Upstream catalog tables are rebuilt into a new ``model_version_id`` each
    time. This table is the durable annotation layer: a build carries reviewed
    definitions forward and marks them for review when their source changes.
    """

    __module__ = "app.models"
    __tablename__ = "definitions"
    __table_args__ = (
        UniqueConstraint(
            "model_version_id", "object_kind", "object_key",
            name="uq_askdata_definitions_object",
        ),
        CheckConstraint(
            "object_kind IN ('term','attribute','metric')",
            name="ck_askdata_definitions_kind",
        ),
        Index(
            "ix_askdata_definitions_review",
            "connection_id",
            "review_required",
        ),
        {"schema": "askdata"},
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    connection_id: Mapped[UUID] = mapped_column(
        ForeignKey("service_databases.id"), index=True
    )
    model_version_id: Mapped[UUID] = mapped_column(
        ForeignKey("askdata.model_versions.id"), index=True
    )
    object_kind: Mapped[str] = mapped_column(String(20))
    object_key: Mapped[str] = mapped_column(String(300))
    object_id: Mapped[str | None] = mapped_column(String(100))
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)
    certified: Mapped[bool] = mapped_column(Boolean, default=False)
    edited_by: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"))
    source_fingerprint: Mapped[str] = mapped_column(String(200), default="")
    review_required: Mapped[bool] = mapped_column(Boolean, default=False)
    review_note: Mapped[str] = mapped_column(String(300), default="")
