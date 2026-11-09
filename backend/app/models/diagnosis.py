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
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class DiagnosisPolicy(TimestampMixin, Base):
    __module__ = "app.models"
    __tablename__ = "diagnosis_policy"
    __table_args__ = (CheckConstraint("id = 1", name="singleton"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    configured_by: Mapped[UUID] = mapped_column(ForeignKey("users.id"))


class AgentAnalysis(TimestampMixin, Base):
    # Keep the public identity and SQLAlchemy write ordering.
    __module__ = "app.models"

    __tablename__ = "agent_analyses"
    __table_args__ = (
        Index(
            "uq_agent_analyses_automatic_incident",
            "incident_id",
            unique=True,
            postgresql_where=text("trigger = 'automatic'"),
        ),
        CheckConstraint("trigger IN ('manual','automatic')", name="trigger"),
        Index(
            "uq_agent_analyses_active_incident",
            "incident_id",
            unique=True,
            postgresql_where=text("status IN ('queued', 'running') AND deleted_at IS NULL"),
        ),
        CheckConstraint(
            "status IN ('queued','running','completed','failed','interrupted')", name="status"
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    incident_id: Mapped[UUID] = mapped_column(ForeignKey("incidents.id"), index=True)
    requested_by: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    trigger: Mapped[str] = mapped_column(String(20), default="manual", server_default="manual")
    status: Mapped[str] = mapped_column(String(20), default="queued")
    input_hash: Mapped[str] = mapped_column(String(64))
    config: Mapped[dict] = mapped_column(JSON)
    source_context: Mapped[dict] = mapped_column(JSON, default=dict, server_default="{}")
    database_context: Mapped[dict] = mapped_column(JSON, default=dict, server_default="{}")
    session_id: Mapped[str | None] = mapped_column(String(100))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(50))
    evidence: Mapped[list] = mapped_column(JSON, default=list)
    knowledge: Mapped[list] = mapped_column(JSON, default=list)
    tool_calls: Mapped[list] = mapped_column(JSON, default=list)
    result: Mapped[dict] = mapped_column(JSON, default=dict)


class AnalysisConversation(TimestampMixin, Base):
    # Keep the public identity and SQLAlchemy write ordering.
    __module__ = "app.models"

    __tablename__ = "analysis_conversations"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    incident_id: Mapped[UUID] = mapped_column(ForeignKey("incidents.id"), index=True)
    owner_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), index=True)
    title: Mapped[str] = mapped_column(String(200))
    last_seq: Mapped[int] = mapped_column(Integer, default=0)


class AnalysisRun(TimestampMixin, Base):
    # Keep the public identity and SQLAlchemy write ordering.
    __module__ = "app.models"

    __tablename__ = "analysis_runs"
    __table_args__ = (
        Index(
            "uq_analysis_runs_active_conversation",
            "conversation_id",
            unique=True,
            postgresql_where=text(
                "status IN ('queued','running','cancelling') AND deleted_at IS NULL"
            ),
        ),
        CheckConstraint(
            "status IN ('queued','running','cancelling','completed','failed',"
            "'cancelled','interrupted')",
            name="status",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    conversation_id: Mapped[UUID] = mapped_column(
        ForeignKey("analysis_conversations.id"), index=True
    )
    incident_id: Mapped[UUID] = mapped_column(ForeignKey("incidents.id"))
    requested_by: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    status: Mapped[str] = mapped_column(String(20), default="queued")
    config: Mapped[dict] = mapped_column(JSON)
    source_context: Mapped[dict] = mapped_column(JSON, default=dict, server_default="{}")
    database_context: Mapped[dict] = mapped_column(JSON, default=dict, server_default="{}")
    session_id: Mapped[str | None] = mapped_column(String(100))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(50))
    model_requests: Mapped[int] = mapped_column(Integer, default=0)
    tool_count: Mapped[int] = mapped_column(Integer, default=0)
    evidence: Mapped[list] = mapped_column(JSON, default=list)
    knowledge: Mapped[list] = mapped_column(JSON, default=list)
    tool_calls: Mapped[list] = mapped_column(JSON, default=list)
    candidate: Mapped[dict] = mapped_column(JSON, default=dict)
    adopted_analysis_id: Mapped[UUID | None] = mapped_column(ForeignKey("agent_analyses.id"))


class AnalysisMessage(TimestampMixin, Base):
    # Keep the public identity and SQLAlchemy write ordering.
    __module__ = "app.models"

    __tablename__ = "analysis_messages"
    __table_args__ = (UniqueConstraint("conversation_id", "client_message_id"),)

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    conversation_id: Mapped[UUID] = mapped_column(
        ForeignKey("analysis_conversations.id"), index=True
    )
    run_id: Mapped[UUID | None] = mapped_column(ForeignKey("analysis_runs.id"), index=True)
    role: Mapped[str] = mapped_column(String(20))
    content: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(30))
    client_message_id: Mapped[UUID | None] = mapped_column()
    sdk_message_id: Mapped[str | None] = mapped_column(String(100))
    stream_key: Mapped[str | None] = mapped_column(String(100))


class AnalysisEvent(TimestampMixin, Base):
    # Keep the public identity and SQLAlchemy write ordering.
    __module__ = "app.models"

    __tablename__ = "analysis_events"
    __table_args__ = (UniqueConstraint("conversation_id", "seq"),)

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    conversation_id: Mapped[UUID] = mapped_column(
        ForeignKey("analysis_conversations.id"), index=True
    )
    run_id: Mapped[UUID | None] = mapped_column(ForeignKey("analysis_runs.id"), index=True)
    seq: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(50))
    data: Mapped[dict] = mapped_column(JSON)
