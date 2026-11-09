"""Role defaults, resource restrictions and independently revocable grants."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class RolePermission(TimestampMixin, Base):
    __module__ = "app.models"
    __tablename__ = "role_permissions"
    __table_args__ = (UniqueConstraint("role_id", "permission"),)

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    role_id: Mapped[UUID] = mapped_column(ForeignKey("roles.id"), index=True)
    permission: Mapped[str] = mapped_column(String(80))


class AccessPolicy(TimestampMixin, Base):
    __module__ = "app.models"
    __tablename__ = "access_policies"
    __table_args__ = (
        UniqueConstraint(
            "resource_kind", "resource_id", "user_id", postgresql_nulls_not_distinct=True
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    resource_kind: Mapped[str] = mapped_column(String(30))
    resource_id: Mapped[str] = mapped_column(String(100))
    user_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"), index=True)
    restricted: Mapped[bool] = mapped_column(Boolean, default=False)
    version: Mapped[int] = mapped_column(Integer, default=1)
    updated_by: Mapped[UUID] = mapped_column(ForeignKey("users.id"))


class AccessRequest(TimestampMixin, Base):
    __module__ = "app.models"
    __tablename__ = "access_requests"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending','approved','rejected','withdrawn','invalidated')", name="status"
        ),
        UniqueConstraint("user_id", "idempotency_key"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), index=True)
    idempotency_key: Mapped[UUID] = mapped_column()
    items: Mapped[list] = mapped_column(JSON)
    reason: Mapped[str] = mapped_column(String(2000))
    duration_days: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    decided_by: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decision_comment: Mapped[str] = mapped_column(String(2000), default="")


class AccessGrant(TimestampMixin, Base):
    __module__ = "app.models"
    __tablename__ = "access_grants"
    __table_args__ = (UniqueConstraint("request_id", "permission", "resource_kind", "resource_id"),)

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    request_id: Mapped[UUID] = mapped_column(ForeignKey("access_requests.id"), index=True)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), index=True)
    permission: Mapped[str] = mapped_column(String(80))
    resource_kind: Mapped[str] = mapped_column(String(30))
    resource_id: Mapped[str] = mapped_column(String(100))
    include_descendants: Mapped[bool] = mapped_column(Boolean, default=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_by: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"))
