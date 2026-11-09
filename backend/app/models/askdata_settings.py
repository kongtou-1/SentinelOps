"""Durable ask-data governance records and saved analyses.

These tables belong to the connection, not to one semantic build: zones, tags,
business rules, sensitive-field policies, supplementary definitions and saved
analyses are human decisions that must survive a rebuild. Build-scoped catalog
and definition rows stay in ``askdata`` version tables.
"""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin

OBJECT_KINDS = ("table", "column", "term", "metric", "code_rule")
RULE_KINDS = (
    "statistical_scope",
    "time_semantics",
    "unit",
    "filter",
    "soft_delete",
    "status",
)
SENSITIVE_CATEGORIES = (
    "mobile",
    "id_card",
    "bank_card",
    "email",
    "address",
    "name",
    "amount",
    "credential",
    "other",
)


class AskDataAnalysis(TimestampMixin, Base):
    """A saved question, SQL, chart and business note, replayed on demand."""

    __module__ = "app.models"
    __tablename__ = "analyses"
    __table_args__ = (
        UniqueConstraint(
            "connection_id", "owner_id", "name", name="uq_askdata_analyses_name"
        ),
        CheckConstraint(
            "last_status IN ('completed','failed','cancelled','')",
            name="ck_askdata_analyses_last_status",
        ),
        Index("ix_askdata_analyses_owner", "connection_id", "owner_id"),
        {"schema": "askdata"},
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    connection_id: Mapped[UUID] = mapped_column(
        ForeignKey("service_databases.id"), index=True
    )
    model_version_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("askdata.model_versions.id")
    )
    owner_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    category: Mapped[str] = mapped_column(String(60), default="")
    question: Mapped[str] = mapped_column(Text)
    sql_text: Mapped[str] = mapped_column(Text)
    explanation: Mapped[str] = mapped_column(Text, default="")
    chart: Mapped[dict] = mapped_column(JSONB, default=dict)
    evidence: Mapped[list] = mapped_column(JSONB, default=list)
    source_run_id: Mapped[UUID | None] = mapped_column(ForeignKey("askdata.runs.id"))
    last_status: Mapped[str] = mapped_column(String(20), default="")
    last_error_code: Mapped[str | None] = mapped_column(String(80))
    last_row_count: Mapped[int | None] = mapped_column(Integer)
    last_duration_ms: Mapped[int | None] = mapped_column(Integer)
    last_executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AskDataZone(TimestampMixin, Base):
    """Business partition used to group catalog objects for retrieval."""

    __module__ = "app.models"
    __tablename__ = "zones"
    __table_args__ = (
        UniqueConstraint("connection_id", "name", name="uq_askdata_zones_name"),
        {"schema": "askdata"},
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    connection_id: Mapped[UUID] = mapped_column(
        ForeignKey("service_databases.id"), index=True
    )
    name: Mapped[str] = mapped_column(String(100))
    description: Mapped[str] = mapped_column(String(300), default="")
    targets: Mapped[list] = mapped_column(JSONB, default=list)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_by: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"))


class AskDataTag(TimestampMixin, Base):
    __module__ = "app.models"
    __tablename__ = "tags"
    __table_args__ = (
        UniqueConstraint("connection_id", "name", name="uq_askdata_tags_name"),
        {"schema": "askdata"},
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    connection_id: Mapped[UUID] = mapped_column(
        ForeignKey("service_databases.id"), index=True
    )
    name: Mapped[str] = mapped_column(String(60))
    color: Mapped[str] = mapped_column(String(20), default="")
    description: Mapped[str] = mapped_column(String(300), default="")
    updated_by: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"))


class AskDataTagTarget(TimestampMixin, Base):
    __module__ = "app.models"
    __tablename__ = "tag_targets"
    __table_args__ = (
        UniqueConstraint(
            "tag_id", "object_kind", "object_key", name="uq_askdata_tag_targets_object"
        ),
        CheckConstraint(
            "object_kind IN ('table','column','term','metric','code_rule')",
            name="ck_askdata_tag_targets_kind",
        ),
        {"schema": "askdata"},
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    connection_id: Mapped[UUID] = mapped_column(
        ForeignKey("service_databases.id"), index=True
    )
    tag_id: Mapped[UUID] = mapped_column(
        ForeignKey("askdata.tags.id", ondelete="CASCADE"), index=True
    )
    object_kind: Mapped[str] = mapped_column(String(20))
    object_key: Mapped[str] = mapped_column(String(300))


class AskDataRule(TimestampMixin, Base):
    """Business rule injected into the question-answering context."""

    __module__ = "app.models"
    __tablename__ = "rules"
    __table_args__ = (
        UniqueConstraint("connection_id", "name", name="uq_askdata_rules_name"),
        CheckConstraint(
            "kind IN ('statistical_scope','time_semantics','unit','filter',"
            "'soft_delete','status')",
            name="ck_askdata_rules_kind",
        ),
        {"schema": "askdata"},
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    connection_id: Mapped[UUID] = mapped_column(
        ForeignKey("service_databases.id"), index=True
    )
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(String(300), default="")
    kind: Mapped[str] = mapped_column(String(30))
    target: Mapped[str] = mapped_column(String(300), default="")
    expression: Mapped[str] = mapped_column(Text, default="")
    unit: Mapped[str] = mapped_column(String(30), default="")
    priority: Mapped[int] = mapped_column(Integer, default=100)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_by: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"))


class AskDataSensitiveField(TimestampMixin, Base):
    """Field-level sampling and masking policy, enforced by the platform."""

    __module__ = "app.models"
    __tablename__ = "sensitive_fields"
    __table_args__ = (
        UniqueConstraint(
            "connection_id", "object_key", name="uq_askdata_sensitive_fields_object"
        ),
        CheckConstraint(
            "category IN ('mobile','id_card','bank_card','email','address','name',"
            "'amount','credential','other')",
            name="ck_askdata_sensitive_fields_category",
        ),
        CheckConstraint(
            "policy IN ('mask','exclude')", name="ck_askdata_sensitive_fields_policy"
        ),
        CheckConstraint(
            "mask_strategy IN ('keep_prefix','keep_suffix','hash','full')",
            name="ck_askdata_sensitive_fields_mask",
        ),
        CheckConstraint(
            "sampling IN ('allow','exclude')",
            name="ck_askdata_sensitive_fields_sampling",
        ),
        {"schema": "askdata"},
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    connection_id: Mapped[UUID] = mapped_column(
        ForeignKey("service_databases.id"), index=True
    )
    # ``schema.table`` or ``schema.table.column``. A table row covers every column.
    object_key: Mapped[str] = mapped_column(String(300))
    category: Mapped[str] = mapped_column(String(20), default="other")
    policy: Mapped[str] = mapped_column(String(12), default="mask")
    mask_strategy: Mapped[str] = mapped_column(String(20), default="full")
    mask_argument: Mapped[int] = mapped_column(Integer, default=0)
    sampling: Mapped[str] = mapped_column(String(12), default="exclude")
    note: Mapped[str] = mapped_column(String(300), default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_by: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"))


class AskDataSupplement(TimestampMixin, Base):
    __module__ = "app.models"
    __tablename__ = "supplements"
    __table_args__ = (
        UniqueConstraint(
            "connection_id", "term", name="uq_askdata_supplements_term"
        ),
        CheckConstraint(
            "kind IN ('abbreviation','alias','context')",
            name="ck_askdata_supplements_kind",
        ),
        {"schema": "askdata"},
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    connection_id: Mapped[UUID] = mapped_column(
        ForeignKey("service_databases.id"), index=True
    )
    kind: Mapped[str] = mapped_column(String(20), default="alias")
    term: Mapped[str] = mapped_column(String(120))
    definition: Mapped[str] = mapped_column(Text, default="")
    aliases: Mapped[list] = mapped_column(JSONB, default=list)
    applies_to: Mapped[str] = mapped_column(String(300), default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_by: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"))


class AskDataImport(TimestampMixin, Base):
    """Reviewed import document; apply re-validates against current grants."""

    __module__ = "app.models"
    __tablename__ = "imports"
    __table_args__ = (
        CheckConstraint(
            "status IN ('previewed','applied','failed')",
            name="ck_askdata_imports_status",
        ),
        {"schema": "askdata"},
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    connection_id: Mapped[UUID] = mapped_column(
        ForeignKey("service_databases.id"), index=True
    )
    model_version_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("askdata.model_versions.id")
    )
    requested_by: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    filename: Mapped[str] = mapped_column(String(200), default="")
    status: Mapped[str] = mapped_column(String(20), default="previewed")
    conflict_policy: Mapped[str] = mapped_column(String(20), default="skip")
    document: Mapped[dict] = mapped_column(JSONB, default=dict)
    report: Mapped[dict] = mapped_column(JSONB, default=dict)
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
