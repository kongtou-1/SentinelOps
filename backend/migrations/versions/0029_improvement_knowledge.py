"""Frozen table DDL; UTC timestamps and update triggers included."""

import sqlalchemy as sa
from alembic import op

revision = "0029_improvement_knowledge"
down_revision = "0028_effect_evaluation"
branch_labels = None
depends_on = None

STATEMENTS = [
    "CREATE TABLE improvement_tasks (\n"
    "\tid UUID NOT NULL, \n"
    "\treport_id UUID NOT NULL, \n"
    "\treport_revision INTEGER NOT NULL, \n"
    "\titem_index INTEGER NOT NULL, \n"
    "\tincident_id UUID NOT NULL, \n"
    "\titem JSON NOT NULL, \n"
    "\ttitle VARCHAR(2000) NOT NULL, \n"
    "\tstatus VARCHAR(30) NOT NULL, \n"
    "\tassignee_id UUID, \n"
    "\trequested_by UUID NOT NULL, \n"
    "\tdue_at TIMESTAMP WITH TIME ZONE, \n"
    "\tacceptance VARCHAR(8000) NOT NULL, \n"
    "\tsubmission JSON NOT NULL, \n"
    "\tdecision JSON NOT NULL, \n"
    "\tversion INTEGER NOT NULL, \n"
    "\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n"
    "\tupdated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n"
    "\tdeleted_at TIMESTAMP WITH TIME ZONE, \n"
    "\tCONSTRAINT pk_improvement_tasks PRIMARY KEY (id), \n"
    "\tCONSTRAINT uq_improvement_tasks_report_id UNIQUE (report_id, report_revision, "
    "item_index), \n"
    "\tCONSTRAINT fk_improvement_tasks_report_id_postmortem_reports FOREIGN KEY(report_id) "
    "REFERENCES postmortem_reports (id), \n"
    "\tCONSTRAINT fk_improvement_tasks_incident_id_incidents FOREIGN KEY(incident_id) "
    "REFERENCES incidents (id), \n"
    "\tCONSTRAINT fk_improvement_tasks_assignee_id_users FOREIGN KEY(assignee_id) "
    "REFERENCES users (id), \n"
    "\tCONSTRAINT fk_improvement_tasks_requested_by_users FOREIGN KEY(requested_by) "
    "REFERENCES users (id)\n"
    ")",
    "CREATE INDEX ix_improvement_tasks_report_id ON improvement_tasks (report_id)",
    "CREATE TRIGGER trg_improvement_tasks_updated_at BEFORE UPDATE ON improvement_tasks FOR "
    "EACH ROW EXECUTE FUNCTION set_updated_at()",
]


def upgrade():
    for table in ("knowledge_documents", "knowledge_skills"):
        op.add_column(
            table, sa.Column("application_ids", sa.JSON(), nullable=False, server_default="[]")
        )
        op.add_column(
            table, sa.Column("applicable_versions", sa.JSON(), nullable=False, server_default="[]")
        )
        op.add_column(table, sa.Column("owner_id", sa.Uuid(), sa.ForeignKey("users.id")))
        op.add_column(table, sa.Column("review_at", sa.DateTime(timezone=True)))
        op.add_column(table, sa.Column("expires_at", sa.DateTime(timezone=True)))
    for statement in STATEMENTS:
        op.execute(statement)


def downgrade():
    op.drop_table("improvement_tasks")
    for table in ("knowledge_documents", "knowledge_skills"):
        for column in (
            "expires_at",
            "review_at",
            "owner_id",
            "applicable_versions",
            "application_ids",
        ):
            op.drop_column(table, column)
