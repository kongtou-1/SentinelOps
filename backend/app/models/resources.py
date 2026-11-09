from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class SSHCredential(TimestampMixin, Base):
    # Keep the public identity and SQLAlchemy write ordering.
    __module__ = "app.models"

    __tablename__ = "ssh_credentials"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    auth_type: Mapped[str] = mapped_column(String(20))
    encrypted_secret: Mapped[str] = mapped_column(Text)


class Server(TimestampMixin, Base):
    # Keep the public identity and SQLAlchemy write ordering.
    __module__ = "app.models"

    __tablename__ = "servers"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    host: Mapped[str] = mapped_column(String(253))
    ssh_port: Mapped[int] = mapped_column(Integer, default=22)
    ssh_user: Mapped[str] = mapped_column(String(64), default="ops")
    credential_id: Mapped[UUID | None] = mapped_column(ForeignKey("ssh_credentials.id"))
    ssh_host_key: Mapped[str | None] = mapped_column(Text)
    environment: Mapped[str] = mapped_column(String(50), default="test")
    node_exporter_target: Mapped[str] = mapped_column(String(300))
    cadvisor_target: Mapped[str] = mapped_column(String(300))
    docker_state_target: Mapped[str | None] = mapped_column(String(300))
    blackbox_exporter_target: Mapped[str | None] = mapped_column(String(300))
    collection_config_changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    health_url: Mapped[str | None] = mapped_column(String(2000))
    monitoring_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    last_ssh_test_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_ssh_test_status: Mapped[str | None] = mapped_column(String(30))


class Container(TimestampMixin, Base):
    # Keep the public identity and SQLAlchemy write ordering.
    __module__ = "app.models"

    __tablename__ = "containers"
    __table_args__ = (UniqueConstraint("server_id", "docker_id"),)

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    server_id: Mapped[UUID] = mapped_column(ForeignKey("servers.id"), index=True)
    docker_id: Mapped[str] = mapped_column(String(300))
    application_id: Mapped[UUID | None] = mapped_column(ForeignKey("applications.id"), index=True)
    name: Mapped[str] = mapped_column(String(300))
    image: Mapped[str] = mapped_column(String(500))
    status: Mapped[str] = mapped_column(String(20))
    labels: Mapped[dict] = mapped_column(JSON)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    image_id: Mapped[str | None] = mapped_column(String(71))
    identity: Mapped[dict] = mapped_column(JSON, default=dict, server_default="{}")
    identity_observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    service_name: Mapped[str] = mapped_column(String(100), default="", server_default="")
    purpose: Mapped[str] = mapped_column(String(2000), default="", server_default="")
    depends_on: Mapped[list] = mapped_column(JSON, default=list, server_default="[]")
