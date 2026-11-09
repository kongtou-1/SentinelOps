"""Source-code business rules with durable repository provenance.

Rows belong to one ``model_version_id`` and are never updated in place by a
later build. Human revisions live on the row they were applied to; the next
build carries them forward and flags changed or removed code for review.
"""

from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
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


class AskDataCodeRule(TimestampMixin, Base):
    __module__ = "app.models"
    __tablename__ = "code_rules"
    __table_args__ = (
        UniqueConstraint(
            "model_version_id", "fingerprint", name="uq_askdata_code_rules_fingerprint"
        ),
        CheckConstraint(
            "language IN ('python','java','other')", name="ck_askdata_code_rules_language"
        ),
        CheckConstraint(
            "parse_state IN ('static','dynamic')", name="ck_askdata_code_rules_parse_state"
        ),
        CheckConstraint("source IN ('auto','manual')", name="ck_askdata_code_rules_source"),
        CheckConstraint(
            "conflict_state IN ('none','pending','resolved')",
            name="ck_askdata_code_rules_conflict_state",
        ),
        Index("ix_askdata_code_rules_review", "model_version_id", "review_required"),
        Index(
            "ix_askdata_code_rules_source",
            "model_version_id",
            "path",
            "rule_kind",
            "object_key",
        ),
        {"schema": "askdata"},
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    connection_id: Mapped[UUID] = mapped_column(ForeignKey("service_databases.id"), index=True)
    model_version_id: Mapped[UUID] = mapped_column(
        ForeignKey("askdata.model_versions.id"), index=True
    )
    source_snapshot_id: Mapped[UUID | None] = mapped_column(ForeignKey("source_snapshots.id"))
    git_commit: Mapped[str | None] = mapped_column(String(64))
    svn_revision: Mapped[str | None] = mapped_column(String(40))
    content_hash: Mapped[str] = mapped_column(String(64), default="")
    language: Mapped[str] = mapped_column(String(10))
    path: Mapped[str] = mapped_column(String(500))
    start_line: Mapped[int] = mapped_column(Integer)
    end_line: Mapped[int] = mapped_column(Integer)
    snippet_hash: Mapped[str] = mapped_column(String(64), default="")
    rule_kind: Mapped[str] = mapped_column(String(30))
    object_key: Mapped[str] = mapped_column(String(300), default="")
    expression: Mapped[str] = mapped_column(Text, default="")
    sql_text: Mapped[str] = mapped_column(Text, default="")
    detail: Mapped[dict] = mapped_column(JSONB, default=dict)
    parse_state: Mapped[str] = mapped_column(String(12), default="static")
    source: Mapped[str] = mapped_column(String(12), default="auto")
    fingerprint: Mapped[str] = mapped_column(String(64), default="")
    certified: Mapped[bool] = mapped_column(Boolean, default=False)
    edited_by: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"))
    review_required: Mapped[bool] = mapped_column(Boolean, default=False)
    review_note: Mapped[str] = mapped_column(String(300), default="")
    conflict_with: Mapped[UUID | None] = mapped_column()
    conflict_state: Mapped[str] = mapped_column(String(12), default="none")
    superseded: Mapped[bool] = mapped_column(Boolean, default=False)
