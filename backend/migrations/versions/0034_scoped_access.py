"""Configurable role defaults and scoped access requests."""

from uuid import uuid4

import sqlalchemy as sa
from alembic import op

revision = "0034_scoped_access"
down_revision = "0033_inspection_sources"
branch_labels = None
depends_on = None

DEFAULTS = {
    "admin": [
        "access:manage",
        "alerts:ack",
        "alerts:read",
        "analyses:read",
        "analyses:run",
        "analysis_conversations:read_all",
        "applications:read",
        "approvals:decide",
        "audit:read",
        "containers:diagnose",
        "containers:read",
        "containers:sync",
        "credentials:manage",
        "dashboards:read",
        "executions:read",
        "executions:run",
        "incidents:force_close",
        "incidents:handle",
        "incidents:read",
        "inspections:manage",
        "inspections:read",
        "inspections:review",
        "inspections:run",
        "knowledge:manage",
        "knowledge:read",
        "llm:manage",
        "llm:probe",
        "llm:read",
        "logs:read",
        "market:manage",
        "market:read",
        "market:review",
        "market:submit",
        "metrics:read",
        "predictions:manage",
        "predictions:read",
        "predictions:run",
        "profile:read",
        "repairs:read",
        "repairs:write",
        "reports:manage",
        "reports:read",
        "reports:run",
        "resources:edit",
        "resources:manage",
        "resources:read",
        "roles:read",
        "rules:manage",
        "servers:read",
        "service_databases:read",
        "service_monitors:read",
        "sources:manage",
        "sources:read",
        "sources:sync",
        "system:read",
        "users:manage",
    ],
    "operator": [
        "alerts:ack",
        "alerts:read",
        "analyses:read",
        "analyses:run",
        "applications:read",
        "containers:diagnose",
        "containers:read",
        "containers:sync",
        "dashboards:read",
        "executions:read",
        "executions:run",
        "incidents:handle",
        "incidents:read",
        "inspections:read",
        "inspections:review",
        "inspections:run",
        "knowledge:read",
        "llm:probe",
        "llm:read",
        "logs:read",
        "market:read",
        "market:submit",
        "metrics:read",
        "predictions:read",
        "predictions:run",
        "profile:read",
        "repairs:read",
        "repairs:write",
        "reports:read",
        "reports:run",
        "resources:read",
        "servers:read",
        "service_databases:read",
        "service_monitors:read",
        "sources:read",
        "sources:sync",
        "system:read",
    ],
    "viewer": [
        "alerts:read",
        "analyses:read",
        "applications:read",
        "containers:read",
        "dashboards:read",
        "executions:read",
        "incidents:read",
        "inspections:read",
        "knowledge:read",
        "llm:read",
        "market:read",
        "market:submit",
        "metrics:read",
        "predictions:read",
        "profile:read",
        "repairs:read",
        "reports:read",
        "resources:read",
        "servers:read",
        "service_databases:read",
        "service_monitors:read",
        "sources:read",
    ],
}

TABLE_DDL = (
    """
CREATE TABLE role_permissions (
        id UUID NOT NULL,
        role_id UUID NOT NULL,
        permission VARCHAR(80) NOT NULL,
        created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
        updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
        deleted_at TIMESTAMP WITH TIME ZONE,
        CONSTRAINT pk_role_permissions PRIMARY KEY (id),
        CONSTRAINT uq_role_permissions_role_id UNIQUE (role_id, permission),
        CONSTRAINT fk_role_permissions_role_id_roles FOREIGN KEY(role_id) REFERENCES roles (id)
)
""",
    """
CREATE INDEX ix_role_permissions_role_id ON role_permissions (role_id)
""",
    """
CREATE TABLE access_policies (
        id UUID NOT NULL,
        resource_kind VARCHAR(30) NOT NULL,
        resource_id VARCHAR(100) NOT NULL,
        restricted BOOLEAN NOT NULL,
        version INTEGER NOT NULL,
        updated_by UUID NOT NULL,
        created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
        updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
        deleted_at TIMESTAMP WITH TIME ZONE,
        CONSTRAINT pk_access_policies PRIMARY KEY (id),
        CONSTRAINT uq_access_policies_resource_kind UNIQUE (resource_kind, resource_id),
        CONSTRAINT fk_access_policies_updated_by_users FOREIGN KEY(updated_by) REFERENCES users
(id)
)
""",
    """
CREATE TABLE access_requests (
        id UUID NOT NULL,
        user_id UUID NOT NULL,
        idempotency_key UUID NOT NULL,
        items JSON NOT NULL,
        reason VARCHAR(2000) NOT NULL,
        duration_days INTEGER,
        status VARCHAR(20) NOT NULL,
        decided_by UUID,
        decided_at TIMESTAMP WITH TIME ZONE,
        decision_comment VARCHAR(2000) NOT NULL,
        created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
        updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
        deleted_at TIMESTAMP WITH TIME ZONE,
        CONSTRAINT pk_access_requests PRIMARY KEY (id),
        CONSTRAINT ck_access_requests_status CHECK (status IN
('pending','approved','rejected','withdrawn','invalidated')),
        CONSTRAINT uq_access_requests_user_id UNIQUE (user_id, idempotency_key),
        CONSTRAINT fk_access_requests_user_id_users FOREIGN KEY(user_id) REFERENCES users (id),
        CONSTRAINT fk_access_requests_decided_by_users FOREIGN KEY(decided_by) REFERENCES users
(id)
)
""",
    """
CREATE INDEX ix_access_requests_user_id ON access_requests (user_id)
""",
    """
CREATE TABLE access_grants (
        id UUID NOT NULL,
        request_id UUID NOT NULL,
        user_id UUID NOT NULL,
        permission VARCHAR(80) NOT NULL,
        resource_kind VARCHAR(30) NOT NULL,
        resource_id VARCHAR(100) NOT NULL,
        include_descendants BOOLEAN NOT NULL,
        expires_at TIMESTAMP WITH TIME ZONE,
        revoked_at TIMESTAMP WITH TIME ZONE,
        revoked_by UUID,
        created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
        updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
        deleted_at TIMESTAMP WITH TIME ZONE,
        CONSTRAINT pk_access_grants PRIMARY KEY (id),
        CONSTRAINT uq_access_grants_request_id UNIQUE (request_id, permission, resource_kind,
resource_id),
        CONSTRAINT fk_access_grants_request_id_access_requests FOREIGN KEY(request_id)
REFERENCES access_requests (id),
        CONSTRAINT fk_access_grants_user_id_users FOREIGN KEY(user_id) REFERENCES users (id),
        CONSTRAINT fk_access_grants_revoked_by_users FOREIGN KEY(revoked_by) REFERENCES users
(id)
)
""",
    """
CREATE INDEX ix_access_grants_request_id ON access_grants (request_id)
""",
    """
CREATE INDEX ix_access_grants_user_id ON access_grants (user_id)
""",
)


def upgrade():
    op.add_column(
        "roles", sa.Column("permissions_version", sa.Integer(), nullable=False, server_default="1")
    )
    for ddl in TABLE_DDL:
        op.execute(sa.text(ddl))
    connection = op.get_bind()
    for role in connection.execute(sa.text("SELECT id, code FROM roles WHERE deleted_at IS NULL")):
        for permission in DEFAULTS[role.code]:
            connection.execute(
                sa.text(
                    "INSERT INTO role_permissions (id, role_id, permission) "
                    "VALUES (:id,:role,:permission)"
                ),
                {"id": uuid4(), "role": role.id, "permission": permission},
            )
    for name in ("role_permissions", "access_policies", "access_requests", "access_grants"):
        op.execute(
            sa.text(
                f"CREATE TRIGGER trg_{name}_updated_at BEFORE UPDATE ON {name} "
                "FOR EACH ROW EXECUTE FUNCTION set_updated_at()"
            )
        )


def downgrade():
    for name in ("access_grants", "access_requests", "access_policies", "role_permissions"):
        op.drop_table(name)
    op.drop_column("roles", "permissions_version")
