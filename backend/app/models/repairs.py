from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    ForeignKey,
    String,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class RepairHandoff(TimestampMixin, Base):
    # Keep the public identity and SQLAlchemy write ordering.
    __module__ = "app.models"

    __tablename__ = "repair_handoffs"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    incident_id: Mapped[UUID] = mapped_column(ForeignKey("incidents.id"), index=True)
    analysis_id: Mapped[UUID] = mapped_column(ForeignKey("agent_analyses.id"))
    application_id: Mapped[UUID | None] = mapped_column(ForeignKey("applications.id"))
    requested_by: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    packet: Mapped[dict] = mapped_column(JSON)
    packet_hash: Mapped[str] = mapped_column(String(64))
    results: Mapped[list] = mapped_column(JSON, default=list)
    verifications: Mapped[list] = mapped_column(JSON, default=list)
