from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class ImprovementTask(TimestampMixin, Base):
    __module__ = "app.models"
    __tablename__ = "improvement_tasks"
    __table_args__ = (UniqueConstraint("report_id", "report_revision", "item_index"),)

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    report_id: Mapped[UUID] = mapped_column(ForeignKey("postmortem_reports.id"), index=True)
    report_revision: Mapped[int] = mapped_column(Integer)
    item_index: Mapped[int] = mapped_column(Integer)
    incident_id: Mapped[UUID] = mapped_column(ForeignKey("incidents.id"))
    item: Mapped[dict] = mapped_column(JSON)
    title: Mapped[str] = mapped_column(String(2000))
    status: Mapped[str] = mapped_column(String(30), default="unassigned")
    assignee_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"))
    requested_by: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    acceptance: Mapped[str] = mapped_column(String(8000), default="")
    submission: Mapped[dict] = mapped_column(JSON, default=dict)
    decision: Mapped[dict] = mapped_column(JSON, default=dict)
    version: Mapped[int] = mapped_column(Integer, default=1)
