from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class PredictionTask(TimestampMixin, Base):
    # Keep the public identity and SQLAlchemy write ordering.
    __module__ = "app.models"

    __tablename__ = "prediction_tasks"
    __table_args__ = (
        CheckConstraint(
            "num_nonnulls(server_id, container_id, llm_endpoint_id) = 1", name="one_resource"
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    server_id: Mapped[UUID | None] = mapped_column(ForeignKey("servers.id"))
    container_id: Mapped[UUID | None] = mapped_column(ForeignKey("containers.id"))
    llm_endpoint_id: Mapped[UUID | None] = mapped_column(ForeignKey("llm_endpoints.id"))
    metric: Mapped[str] = mapped_column(String(50))
    selector: Mapped[dict] = mapped_column(JSON, default=dict)
    threshold: Mapped[float] = mapped_column(Float)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    interval_seconds: Mapped[int] = mapped_column(Integer, default=300)
    next_run_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class PredictionResult(TimestampMixin, Base):
    # Keep the public identity and SQLAlchemy write ordering.
    __module__ = "app.models"

    __tablename__ = "prediction_results"
    __table_args__ = (
        Index(
            "uq_prediction_results_active_task",
            "task_id",
            unique=True,
            postgresql_where=(text("status IN ('queued', 'running') AND deleted_at IS NULL")),
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    task_id: Mapped[UUID] = mapped_column(ForeignKey("prediction_tasks.id"), index=True)
    status: Mapped[str] = mapped_column(String(20), default="queued")
    trigger: Mapped[str] = mapped_column(String(20), default="manual")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    data_end_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(50))
    config: Mapped[dict] = mapped_column(JSON)
    quality: Mapped[dict] = mapped_column(JSON, default=dict)
    horizons: Mapped[dict] = mapped_column(JSON, default=dict)
    anomalies: Mapped[list] = mapped_column(JSON, default=list)
    detector_state: Mapped[dict] = mapped_column(JSON, default=dict)
    backtest: Mapped[dict] = mapped_column(JSON, default=dict)
