from datetime import datetime
from uuid import UUID, uuid4

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class KnowledgeDocument(TimestampMixin, Base):
    # Keep the public identity and SQLAlchemy write ordering.
    __module__ = "app.models"

    __tablename__ = "knowledge_documents"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    title: Mapped[str] = mapped_column(String(200))
    content: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(500), default="")
    tags: Mapped[list] = mapped_column(JSON, default=list)
    version: Mapped[int] = mapped_column(Integer, default=1)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    index_status: Mapped[str] = mapped_column(String(20), default="queued")
    error_code: Mapped[str | None] = mapped_column(String(50))
    application_ids: Mapped[list] = mapped_column(JSON, default=list, server_default="[]")
    applicable_versions: Mapped[list] = mapped_column(JSON, default=list, server_default="[]")
    owner_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"))
    review_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class KnowledgeChunk(TimestampMixin, Base):
    # Keep the public identity and SQLAlchemy write ordering.
    __module__ = "app.models"

    __tablename__ = "knowledge_chunks"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    document_id: Mapped[UUID] = mapped_column(ForeignKey("knowledge_documents.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    position: Mapped[int] = mapped_column(Integer)
    content: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list] = mapped_column(Vector(512))


class KnowledgeSkill(TimestampMixin, Base):
    # Keep the public identity and SQLAlchemy write ordering.
    __module__ = "app.models"

    __tablename__ = "knowledge_skills"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    code: Mapped[str] = mapped_column(String(100), unique=True)
    title: Mapped[str] = mapped_column(String(200))
    content: Mapped[str] = mapped_column(Text)
    scripts: Mapped[dict] = mapped_column(JSON, default=dict, server_default="{}")
    resources: Mapped[dict] = mapped_column(JSON, default=dict, server_default="{}")
    tags: Mapped[list] = mapped_column(JSON, default=list)
    version: Mapped[int] = mapped_column(Integer, default=1)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    application_ids: Mapped[list] = mapped_column(JSON, default=list, server_default="[]")
    applicable_versions: Mapped[list] = mapped_column(JSON, default=list, server_default="[]")
    owner_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"))
    review_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
