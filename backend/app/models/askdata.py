"""Platform-owned lifecycle records and versioned upstream ontology tables."""

from datetime import datetime
from uuid import UUID, uuid4

from auto_ontology.dal.schema import METADATA as UPSTREAM_METADATA
from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    PrimaryKeyConstraint,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class AskDataConfiguration(TimestampMixin, Base):
    __module__ = "app.models"
    __tablename__ = "configurations"
    __table_args__ = ({"schema": "askdata"},)

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    connection_id: Mapped[UUID] = mapped_column(ForeignKey("service_databases.id"), unique=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    active_model_version_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("askdata.model_versions.id")
    )
    # Execution settings stay per connection and are copied into each task.
    budget_seconds: Mapped[int] = mapped_column(Integer, default=180)
    reasoning_channel_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("agent_channels.id")
    )
    light_channel_id: Mapped[UUID | None] = mapped_column(ForeignKey("agent_channels.id"))
    vector_model: Mapped[str] = mapped_column(String(120), default="")
    vector_dimension: Mapped[int] = mapped_column(Integer, default=512)
    reranker_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    reranker_endpoint: Mapped[str] = mapped_column(String(300), default="")
    reranker_model: Mapped[str] = mapped_column(String(120), default="")
    reranker_key_encrypted: Mapped[str | None] = mapped_column(Text)
    reranker_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_by: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"))


class AskDataModelVersion(TimestampMixin, Base):
    __module__ = "app.models"
    __tablename__ = "model_versions"
    __table_args__ = (
        UniqueConstraint("connection_id", "id"),
        CheckConstraint("status IN ('draft','ready','failed','retired')", name="status"),
        {"schema": "askdata"},
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    connection_id: Mapped[UUID] = mapped_column(ForeignKey("service_databases.id"), index=True)
    source_snapshot_id: Mapped[UUID | None] = mapped_column(ForeignKey("source_snapshots.id"))
    created_by: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    config_version: Mapped[int] = mapped_column(Integer)
    schema_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(20), default="draft")
    context: Mapped[dict] = mapped_column(JSON, default=dict)
    vector_signature: Mapped[str] = mapped_column(String(140), default="")
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AskDataBuild(TimestampMixin, Base):
    __module__ = "app.models"
    __tablename__ = "builds"
    __table_args__ = (
        ForeignKeyConstraint(
            ["connection_id", "model_version_id"],
            ["askdata.model_versions.connection_id", "askdata.model_versions.id"],
        ),
        CheckConstraint(
            "status IN ('queued','running','completed','failed','cancelled','interrupted')",
            name="status",
        ),
        {"schema": "askdata"},
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    connection_id: Mapped[UUID] = mapped_column(ForeignKey("service_databases.id"), index=True)
    model_version_id: Mapped[UUID] = mapped_column(Uuid)
    requested_by: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    status: Mapped[str] = mapped_column(String(20), default="queued")
    step: Mapped[str] = mapped_column(String(100), default="")
    error_code: Mapped[str | None] = mapped_column(String(100))
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AskDataConversation(TimestampMixin, Base):
    __module__ = "app.models"
    __tablename__ = "conversations"
    __table_args__ = (UniqueConstraint("connection_id", "id"), {"schema": "askdata"})

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    connection_id: Mapped[UUID] = mapped_column(ForeignKey("service_databases.id"), index=True)
    owner_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), index=True)
    title: Mapped[str] = mapped_column(String(200), default="新问数")


class AskDataRun(TimestampMixin, Base):
    __module__ = "app.models"
    __tablename__ = "runs"
    __table_args__ = (
        ForeignKeyConstraint(
            ["connection_id", "conversation_id"],
            ["askdata.conversations.connection_id", "askdata.conversations.id"],
        ),
        ForeignKeyConstraint(
            ["connection_id", "model_version_id"],
            ["askdata.model_versions.connection_id", "askdata.model_versions.id"],
        ),
        UniqueConstraint("conversation_id", "idempotency_key"),
        CheckConstraint(
            "status IN ('queued','running','completed','needs_clarification',"
            "'failed','cancelled','interrupted')",
            name="status",
        ),
        {"schema": "askdata"},
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    connection_id: Mapped[UUID] = mapped_column(ForeignKey("service_databases.id"), index=True)
    conversation_id: Mapped[UUID] = mapped_column(Uuid, index=True)
    model_version_id: Mapped[UUID] = mapped_column(Uuid)
    idempotency_key: Mapped[UUID] = mapped_column(Uuid)
    status: Mapped[str] = mapped_column(String(30), default="queued")
    question: Mapped[str] = mapped_column(Text)
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    error_code: Mapped[str | None] = mapped_column(String(100))


class AskDataEvent(TimestampMixin, Base):
    __module__ = "app.models"
    __tablename__ = "events"
    __table_args__ = (UniqueConstraint("run_id", "seq"), {"schema": "askdata"})

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    run_id: Mapped[UUID] = mapped_column(ForeignKey("askdata.runs.id"), index=True)
    seq: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(50))
    data: Mapped[dict] = mapped_column(JSON, default=dict)


class AskDataEmbedding(TimestampMixin, Base):
    """Platform-owned vector rows; one collection per semantic build version."""

    __module__ = "app.models"
    __tablename__ = "embeddings"
    __table_args__ = (
        UniqueConstraint("model_version_id", "collection", "path"),
        CheckConstraint("collection IN ('semantic','data')", name="collection"),
        Index(
            "ix_askdata_embeddings_vector",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
            postgresql_with={"m": 16, "ef_construction": 64},
        ),
        {"schema": "askdata"},
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    model_version_id: Mapped[UUID] = mapped_column(
        ForeignKey("askdata.model_versions.id"), index=True
    )
    collection: Mapped[str] = mapped_column(String(20))
    path: Mapped[str] = mapped_column(String(500))
    content: Mapped[str] = mapped_column(Text)
    meta: Mapped[dict] = mapped_column(JSONB, default=dict)
    embedding: Mapped[list] = mapped_column(Vector(512))


# Keep upstream query/table identities intact. Copies belong to platform metadata;
# they are never created by library import or a request. Disabled features have no tables.
ONTOLOGY_TABLES = {}
for _source in UPSTREAM_METADATA.sorted_tables:
    if _source.name in {"pql_analysis", "user_group", "user_group__member"}:
        continue
    _table = _source.to_metadata(Base.metadata, schema="askdata")
    _table.append_column(Column("model_version_id", Uuid, nullable=False))
    _table.append_column(
        Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now())
    )
    _table.append_column(
        Column(
            "updated_at",
            DateTime(timezone=True),
            nullable=False,
            server_default=func.now(),
            onupdate=func.now(),
        )
    )
    _table.append_column(Column("deleted_at", DateTime(timezone=True)))
    _pk_columns = [c.name for c in _table.primary_key]
    _table.c.model_version_id.primary_key = True
    _table.constraints.remove(_table.primary_key)
    _table.append_constraint(
        PrimaryKeyConstraint("model_version_id", *_pk_columns, name=f"pk_{_table.name}")
    )
    for _constraint in list(_table.constraints):
        if isinstance(_constraint, UniqueConstraint):
            _columns = [c.name for c in _constraint.columns]
            _table.constraints.remove(_constraint)
            _table.append_constraint(
                UniqueConstraint("model_version_id", *_columns, name=_constraint.name)
            )
        elif isinstance(_constraint, ForeignKeyConstraint):
            _local = [e.parent.name for e in _constraint.elements]
            _remote = [e.target_fullname for e in _constraint.elements]
            _target = _remote[0].rsplit(".", 1)[0]
            for _element in _constraint.elements:
                _element.parent.foreign_keys.discard(_element)
                _table.foreign_keys.discard(_element)
            _table.constraints.remove(_constraint)
            _table.append_constraint(
                ForeignKeyConstraint(
                    ["model_version_id", *_local],
                    [f"{_target}.model_version_id", *_remote],
                    name=_constraint.name,
                )
            )
    _table.append_constraint(
        ForeignKeyConstraint(["model_version_id"], ["askdata.model_versions.id"])
    )
    for _index in list(_table.indexes):
        if _index.unique:
            _table.indexes.remove(_index)
            Index(
                _index.name,
                _table.c.model_version_id,
                *_index.expressions,
                unique=True,
                **dict(_index.dialect_kwargs),
            )
    if _table.name == "catalog_database":
        # Connection secrets are owned exclusively by ServiceDatabase.
        _table.append_constraint(CheckConstraint("connection IS NULL", name="no_credentials"))
    ONTOLOGY_TABLES[_source.name] = _table
