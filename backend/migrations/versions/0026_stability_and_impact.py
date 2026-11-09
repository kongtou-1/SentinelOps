"""Frozen recovery stability, service policies and append-only incident relationships."""

import sqlalchemy as sa
from alembic import op

revision = "0026_stability_impact"
down_revision = "0025_service_objectives"
branch_labels = None
depends_on = None


def timestamps():
    return [
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
    ]


def upgrade():
    op.create_table(
        "service_operations_policies",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "application_id",
            sa.Uuid(),
            sa.ForeignKey("applications.id"),
            nullable=False,
            unique=True,
        ),
        sa.Column("recovery_seconds", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("response_config", sa.JSON(), nullable=False, server_default="{}"),
        *timestamps(),
    )
    op.create_table(
        "incident_relations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("incident_id", sa.Uuid(), sa.ForeignKey("incidents.id"), nullable=False),
        sa.Column("related_incident_id", sa.Uuid(), sa.ForeignKey("incidents.id"), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("decided_by", sa.Uuid(), sa.ForeignKey("users.id")),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.UniqueConstraint("incident_id", "related_incident_id", "kind"),
        sa.CheckConstraint("status IN ('candidate','confirmed','dismissed')", name="status"),
        sa.CheckConstraint("kind IN ('related','dependency','recurrence')", name="kind"),
        sa.CheckConstraint("incident_id <> related_incident_id", name="different_incidents"),
        *timestamps(),
    )
    for name in ("incident_id", "related_incident_id"):
        op.create_index("ix_incident_relations_" + name, "incident_relations", [name])
    for table in ("incident_relations", "service_operations_policies"):
        op.execute(
            f"CREATE TRIGGER trg_{table}_updated_at BEFORE UPDATE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION set_updated_at()"
        )
    for name in ("stability_started_at", "stability_last_evidence_at"):
        op.add_column("alert_events", sa.Column(name, sa.DateTime(timezone=True)))


def downgrade():
    for name in ("stability_started_at", "stability_last_evidence_at"):
        op.drop_column("alert_events", name)
    op.drop_table("incident_relations")
    op.drop_table("service_operations_policies")
