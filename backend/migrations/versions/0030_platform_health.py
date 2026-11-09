"""Frozen table DDL; UTC timestamps and update triggers included."""

from alembic import op

revision = "0030_platform_health"
down_revision = "0029_improvement_knowledge"
branch_labels = None
depends_on = None

STATEMENTS = [
    "CREATE TABLE backup_records (\n"
    "\tid UUID NOT NULL, \n"
    "\tkind VARCHAR(20) NOT NULL, \n"
    "\tbackup_id UUID, \n"
    "\trequested_by UUID NOT NULL, \n"
    "\tidempotency_key UUID NOT NULL, \n"
    "\trequest_hash VARCHAR(64) NOT NULL, \n"
    "\tartifacts JSON NOT NULL, \n"
    "\tcompleted_at TIMESTAMP WITH TIME ZONE NOT NULL, \n"
    "\tduration_seconds FLOAT, \n"
    "\tverification VARCHAR(20) NOT NULL, \n"
    "\tevidence VARCHAR(8000) NOT NULL, \n"
    "\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n"
    "\tupdated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n"
    "\tdeleted_at TIMESTAMP WITH TIME ZONE, \n"
    "\tCONSTRAINT pk_backup_records PRIMARY KEY (id), \n"
    "\tCONSTRAINT fk_backup_records_backup_id_backup_records FOREIGN KEY(backup_id) "
    "REFERENCES backup_records (id), \n"
    "\tCONSTRAINT fk_backup_records_requested_by_users FOREIGN KEY(requested_by) REFERENCES "
    "users (id), \n"
    "\tCONSTRAINT uq_backup_records_idempotency_key UNIQUE (idempotency_key)\n"
    ")",
    "CREATE INDEX ix_backup_records_completed_at ON backup_records (completed_at)",
    "CREATE TRIGGER trg_backup_records_updated_at BEFORE UPDATE ON backup_records FOR EACH "
    "ROW EXECUTE FUNCTION set_updated_at()",
]


def upgrade():
    for statement in STATEMENTS:
        op.execute(statement)


def downgrade():
    op.drop_table("backup_records")
