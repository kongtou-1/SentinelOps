from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class InspectionDataSource(TimestampMixin, Base):
    __module__ = "app.models"
    __tablename__ = "inspection_data_sources"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    application_id: Mapped[UUID | None] = mapped_column(ForeignKey("applications.id"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    environment: Mapped[str] = mapped_column(String(50))
    source_kind: Mapped[str] = mapped_column(String(20))
    source_config: Mapped[dict] = mapped_column(JSON)
    encrypted_headers: Mapped[str | None] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    last_test_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_test_success: Mapped[bool | None] = mapped_column(Boolean)
    last_error_code: Mapped[str | None] = mapped_column(String(80))


class InspectionPlan(TimestampMixin, Base):
    __module__ = "app.models"
    __tablename__ = "inspection_plans"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    application_id: Mapped[UUID | None] = mapped_column(ForeignKey("applications.id"), index=True)
    configured_by: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    name: Mapped[str] = mapped_column(String(100))
    environment: Mapped[str] = mapped_column(String(50))
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    trial_only: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    version: Mapped[int] = mapped_column(Integer, default=1)
    interval_seconds: Mapped[int] = mapped_column(Integer, default=3600)
    data_source_id: Mapped[UUID | None] = mapped_column(ForeignKey("inspection_data_sources.id"))
    schedule_kind: Mapped[str] = mapped_column(
        String(20), default="interval", server_default="interval"
    )
    cron_expression: Mapped[str] = mapped_column(String(100), default="", server_default="")
    schedule_timezone: Mapped[str] = mapped_column(
        String(100), default="Asia/Shanghai", server_default="Asia/Shanghai"
    )
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    timeout_seconds: Mapped[int] = mapped_column(Integer, default=300, server_default="300")
    max_retries: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    lookback_seconds: Mapped[int] = mapped_column(Integer, default=86400)
    max_records: Mapped[int] = mapped_column(Integer, default=100)
    execution_mode: Mapped[str] = mapped_column(
        String(20), default="records", server_default="records"
    )
    agent_source_ids: Mapped[list] = mapped_column(JSON, default=list, server_default="[]")
    agent_parameters: Mapped[dict] = mapped_column(JSON, default=dict, server_default="{}")
    agent_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    max_agent_items: Mapped[int] = mapped_column(Integer, default=10)
    skill_id: Mapped[UUID | None] = mapped_column(ForeignKey("knowledge_skills.id"))
    skill_version: Mapped[int | None] = mapped_column(Integer)
    source_kind: Mapped[str] = mapped_column(String(20))
    source_config: Mapped[dict] = mapped_column(JSON)
    encrypted_headers: Mapped[str | None] = mapped_column(Text)
    related_sources: Mapped[list] = mapped_column(JSON, default=list, server_default="[]")
    encrypted_related_headers: Mapped[str | None] = mapped_column(Text)
    rules: Mapped[list] = mapped_column(JSON, default=list)
    check_items: Mapped[list] = mapped_column(JSON, default=list, server_default="[]")
    fixed_objects: Mapped[list] = mapped_column(JSON, default=list, server_default="[]")
    object_mode: Mapped[str] = mapped_column(
        String(20), default="dynamic", server_default="dynamic"
    )
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    watermark: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    continuation: Mapped[dict] = mapped_column(JSON, default=dict)


class InspectionRun(TimestampMixin, Base):
    __module__ = "app.models"
    __tablename__ = "inspection_runs"
    __table_args__ = (
        Index(
            "uq_inspection_runs_active_plan",
            "plan_id",
            unique=True,
            postgresql_where=text("status IN ('queued','collecting','evaluating')"),
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    plan_id: Mapped[UUID] = mapped_column(ForeignKey("inspection_plans.id"), index=True)
    plan_version: Mapped[int] = mapped_column(Integer)
    trigger: Mapped[str] = mapped_column(String(20))
    run_kind: Mapped[str] = mapped_column(String(20), default="standard", server_default="standard")
    requested_by: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    status: Mapped[str] = mapped_column(String(20), default="queued", index=True)
    snapshot: Mapped[dict] = mapped_column(JSON)
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    window_end: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    page_cursor: Mapped[dict] = mapped_column(JSON, default=dict)
    collected: Mapped[int] = mapped_column(Integer, default=0)
    agent_calls: Mapped[int] = mapped_column(Integer, default=0)
    summary: Mapped[dict] = mapped_column(JSON, default=dict)
    error_code: Mapped[str | None] = mapped_column(String(80))
    error_stage: Mapped[str | None] = mapped_column(String(30))
    retry_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class InspectionItem(TimestampMixin, Base):
    __module__ = "app.models"
    __tablename__ = "inspection_items"
    __table_args__ = (
        Index("uq_inspection_items_identity", "identity_hash", unique=True),
        Index("ix_inspection_items_object", "plan_id", "record_id", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    run_id: Mapped[UUID] = mapped_column(ForeignKey("inspection_runs.id"), index=True)
    plan_id: Mapped[UUID] = mapped_column(ForeignKey("inspection_plans.id"), index=True)
    identity_hash: Mapped[str] = mapped_column(String(64))
    record_id: Mapped[str] = mapped_column(String(200))
    record_version: Mapped[str] = mapped_column(String(200))
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    evidence: Mapped[dict] = mapped_column(JSON)
    checks: Mapped[list] = mapped_column(JSON, default=list)
    verdict: Mapped[str] = mapped_column(String(30))
    rule_verdict: Mapped[str | None] = mapped_column(String(20))
    check_status: Mapped[str | None] = mapped_column(String(20))
    agent_status: Mapped[str] = mapped_column(String(30), default="disabled")
    agent_result: Mapped[dict] = mapped_column(JSON, default=dict)


class InspectionReview(TimestampMixin, Base):
    __module__ = "app.models"
    __tablename__ = "inspection_reviews"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    item_id: Mapped[UUID] = mapped_column(ForeignKey("inspection_items.id"), index=True)
    reviewer_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    verdict: Mapped[str] = mapped_column(String(30))
    comment: Mapped[str] = mapped_column(String(4000))


class InspectionCheckResult(TimestampMixin, Base):
    __module__ = "app.models"
    __tablename__ = "inspection_check_results"
    __table_args__ = (
        Index("uq_inspection_check_result", "run_id", "object_key", "check_id", unique=True),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    run_id: Mapped[UUID] = mapped_column(ForeignKey("inspection_runs.id"), index=True)
    plan_id: Mapped[UUID] = mapped_column(ForeignKey("inspection_plans.id"), index=True)
    item_id: Mapped[UUID | None] = mapped_column(ForeignKey("inspection_items.id"), index=True)
    object_key: Mapped[str] = mapped_column(String(200))
    check_id: Mapped[str] = mapped_column(String(50))
    kind: Mapped[str] = mapped_column(String(20))
    scope: Mapped[str] = mapped_column(String(20), default="object")
    execution_status: Mapped[str] = mapped_column(String(20), default="pending")
    status: Mapped[str] = mapped_column(String(20), default="unknown")
    severity: Mapped[str | None] = mapped_column(String(20))
    summary: Mapped[str] = mapped_column(String(4000), default="")
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    error_code: Mapped[str | None] = mapped_column(String(80))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class InspectionTrace(TimestampMixin, Base):
    __module__ = "app.models"
    __tablename__ = "inspection_traces"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    run_id: Mapped[UUID] = mapped_column(ForeignKey("inspection_runs.id"), index=True)
    plan_id: Mapped[UUID] = mapped_column(ForeignKey("inspection_plans.id"), index=True)
    check_result_id: Mapped[UUID] = mapped_column(
        ForeignKey("inspection_check_results.id"), index=True
    )
    tool: Mapped[str] = mapped_column(String(80))
    success: Mapped[bool] = mapped_column(Boolean)
    data: Mapped[dict] = mapped_column(JSON)


class InspectionHTTPConnection(TimestampMixin, Base):
    __module__ = "app.models"
    __tablename__ = "inspection_http_connections"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    application_id: Mapped[UUID | None] = mapped_column(ForeignKey("applications.id"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    environment: Mapped[str] = mapped_column(String(50))
    base_url: Mapped[str] = mapped_column(String(2000))
    encrypted_headers: Mapped[str | None] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
