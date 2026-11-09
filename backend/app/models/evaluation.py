from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class DiagnosisReview(TimestampMixin, Base):
    __module__ = "app.models"
    __tablename__ = "diagnosis_reviews"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    analysis_id: Mapped[UUID] = mapped_column(ForeignKey("agent_analyses.id"), index=True)
    reviewer_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    verdict: Mapped[str] = mapped_column(String(20))
    confirmed_cause: Mapped[str] = mapped_column(String(4000))
    evidence: Mapped[str] = mapped_column(String(8000))


class EvaluationCase(TimestampMixin, Base):
    __module__ = "app.models"
    __tablename__ = "evaluation_cases"
    __table_args__ = (UniqueConstraint("family_id", "version"),)

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    family_id: Mapped[UUID] = mapped_column(default=uuid4)
    version: Mapped[int] = mapped_column(Integer, default=1)
    title: Mapped[str] = mapped_column(String(200))
    analysis_id: Mapped[UUID] = mapped_column(ForeignKey("agent_analyses.id"))
    created_by: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    snapshot: Mapped[dict] = mapped_column(JSON)
    content_hash: Mapped[str] = mapped_column(String(64))
    checks: Mapped[dict] = mapped_column(JSON)
    human_label: Mapped[str] = mapped_column(String(4000))


class EvaluationRun(TimestampMixin, Base):
    __module__ = "app.models"
    __tablename__ = "evaluation_runs"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    case_id: Mapped[UUID] = mapped_column(ForeignKey("evaluation_cases.id"), index=True)
    requested_by: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    idempotency_key: Mapped[UUID] = mapped_column(unique=True)
    request_hash: Mapped[str] = mapped_column(String(64))
    provider_kind: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20), default="queued", index=True)
    config: Mapped[dict] = mapped_column(JSON)
    prompt: Mapped[str] = mapped_column(String(8000))
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(80))
    review: Mapped[dict] = mapped_column(JSON, default=dict)
    version: Mapped[int] = mapped_column(Integer, default=1)


class PredictionOutcome(TimestampMixin, Base):
    __module__ = "app.models"
    __tablename__ = "prediction_outcomes"
    __table_args__ = (UniqueConstraint("result_id", "horizon_hours"),)

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    result_id: Mapped[UUID] = mapped_column(ForeignKey("prediction_results.id"), index=True)
    horizon_hours: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20))
    observation: Mapped[dict] = mapped_column(JSON, default=dict)
    incident_links: Mapped[list] = mapped_column(JSON, default=list)
    version: Mapped[int] = mapped_column(Integer, default=1)
