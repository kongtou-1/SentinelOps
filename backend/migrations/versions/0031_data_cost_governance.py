"""Frozen table DDL; UTC timestamps and update triggers included."""

import sqlalchemy as sa
from alembic import op

revision = "0031_data_cost_governance"
down_revision = "0030_platform_health"
branch_labels = None
depends_on = None

STATEMENTS = [
    "CREATE TABLE model_usage (\n"
    "\tid UUID NOT NULL, \n"
    "\tdedupe_key VARCHAR(250) NOT NULL, \n"
    "\tsource VARCHAR(30) NOT NULL, \n"
    "\tprovider_kind VARCHAR(20) NOT NULL, \n"
    "\tapplication_id UUID, \n"
    "\tresource_kind VARCHAR(30) NOT NULL, \n"
    "\tresource_id UUID NOT NULL, \n"
    "\tpurpose VARCHAR(20) NOT NULL, \n"
    "\toperation VARCHAR(100) NOT NULL, \n"
    "\tmodel VARCHAR(100) NOT NULL, \n"
    "\toccurred_at TIMESTAMP WITH TIME ZONE NOT NULL, \n"
    "\tcalls FLOAT, \n"
    "\tinput_tokens FLOAT, \n"
    "\toutput_tokens FLOAT, \n"
    "\tunknown_calls FLOAT, \n"
    "\tduration_seconds FLOAT, \n"
    "\tttft_seconds FLOAT, \n"
    "\toutcome VARCHAR(30) NOT NULL, \n"
    "\tdetail JSON NOT NULL, \n"
    "\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n"
    "\tupdated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n"
    "\tdeleted_at TIMESTAMP WITH TIME ZONE, \n"
    "\tCONSTRAINT pk_model_usage PRIMARY KEY (id), \n"
    "\tCONSTRAINT uq_model_usage_dedupe_key UNIQUE (dedupe_key), \n"
    "\tCONSTRAINT fk_model_usage_application_id_applications FOREIGN KEY(application_id) "
    "REFERENCES applications (id)\n"
    ")",
    "CREATE INDEX ix_model_usage_application_id ON model_usage (application_id)",
    "CREATE INDEX ix_model_usage_occurred_at ON model_usage (occurred_at)",
    "CREATE INDEX ix_model_usage_resource_id ON model_usage (resource_id)",
    "CREATE TRIGGER trg_model_usage_updated_at BEFORE UPDATE ON model_usage FOR EACH ROW "
    "EXECUTE FUNCTION set_updated_at()",
    "CREATE TABLE usage_cursors (\n"
    "\tid UUID NOT NULL, \n"
    "\tapplication_id UUID NOT NULL, \n"
    "\tsource_epoch VARCHAR(100) NOT NULL, \n"
    "\tobserved_until TIMESTAMP WITH TIME ZONE NOT NULL, \n"
    "\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n"
    "\tupdated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n"
    "\tdeleted_at TIMESTAMP WITH TIME ZONE, \n"
    "\tCONSTRAINT pk_usage_cursors PRIMARY KEY (id), \n"
    "\tCONSTRAINT uq_usage_cursors_application_id UNIQUE (application_id), \n"
    "\tCONSTRAINT fk_usage_cursors_application_id_applications FOREIGN KEY(application_id) "
    "REFERENCES applications (id)\n"
    ")",
    "CREATE TRIGGER trg_usage_cursors_updated_at BEFORE UPDATE ON usage_cursors FOR EACH "
    "ROW EXECUTE FUNCTION set_updated_at()",
    "CREATE TABLE model_prices (\n"
    "\tid UUID NOT NULL, \n"
    "\tmodel VARCHAR(100) NOT NULL, \n"
    "\tcurrency VARCHAR(3) NOT NULL, \n"
    "\teffective_at TIMESTAMP WITH TIME ZONE NOT NULL, \n"
    "\tinput_per_million NUMERIC(20, 8) NOT NULL, \n"
    "\toutput_per_million NUMERIC(20, 8) NOT NULL, \n"
    "\tcreated_by UUID NOT NULL, \n"
    "\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n"
    "\tupdated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n"
    "\tdeleted_at TIMESTAMP WITH TIME ZONE, \n"
    "\tCONSTRAINT pk_model_prices PRIMARY KEY (id), \n"
    "\tCONSTRAINT uq_model_prices_model UNIQUE (model, currency, effective_at), \n"
    "\tCONSTRAINT fk_model_prices_created_by_users FOREIGN KEY(created_by) REFERENCES users "
    "(id)\n"
    ")",
    "CREATE TRIGGER trg_model_prices_updated_at BEFORE UPDATE ON model_prices FOR EACH ROW "
    "EXECUTE FUNCTION set_updated_at()",
    "CREATE TABLE retention_plans (\n"
    "\tid UUID NOT NULL, \n"
    "\tcreated_by UUID NOT NULL, \n"
    "\tconfirmed_by UUID, \n"
    "\tstatus VARCHAR(20) NOT NULL, \n"
    "\tcandidates JSON NOT NULL, \n"
    "\tresult JSON NOT NULL, \n"
    "\tversion INTEGER NOT NULL, \n"
    "\tconfirmed_at TIMESTAMP WITH TIME ZONE, \n"
    "\tfinished_at TIMESTAMP WITH TIME ZONE, \n"
    "\terror_code VARCHAR(80), \n"
    "\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n"
    "\tupdated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n"
    "\tdeleted_at TIMESTAMP WITH TIME ZONE, \n"
    "\tCONSTRAINT pk_retention_plans PRIMARY KEY (id), \n"
    "\tCONSTRAINT fk_retention_plans_created_by_users FOREIGN KEY(created_by) REFERENCES "
    "users (id), \n"
    "\tCONSTRAINT fk_retention_plans_confirmed_by_users FOREIGN KEY(confirmed_by) "
    "REFERENCES users (id)\n"
    ")",
    "CREATE TRIGGER trg_retention_plans_updated_at BEFORE UPDATE ON retention_plans FOR "
    "EACH ROW EXECUTE FUNCTION set_updated_at()",
    "CREATE TABLE usage_budgets (\n"
    "\tid UUID NOT NULL, \n"
    "\tname VARCHAR(100) NOT NULL, \n"
    "\tcurrency VARCHAR(3) NOT NULL, \n"
    "\tmonthly_limit NUMERIC(20, 8) NOT NULL, \n"
    "\tapplication_id UUID, \n"
    "\tpurpose VARCHAR(20), \n"
    "\tenabled BOOLEAN NOT NULL, \n"
    "\tversion INTEGER NOT NULL, \n"
    "\tconfigured_by UUID NOT NULL, \n"
    "\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n"
    "\tupdated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n"
    "\tdeleted_at TIMESTAMP WITH TIME ZONE, \n"
    "\tCONSTRAINT pk_usage_budgets PRIMARY KEY (id), \n"
    "\tCONSTRAINT fk_usage_budgets_application_id_applications FOREIGN KEY(application_id) "
    "REFERENCES applications (id), \n"
    "\tCONSTRAINT fk_usage_budgets_configured_by_users FOREIGN KEY(configured_by) "
    "REFERENCES users (id)\n"
    ")",
    "CREATE TRIGGER trg_usage_budgets_updated_at BEFORE UPDATE ON usage_budgets FOR EACH "
    "ROW EXECUTE FUNCTION set_updated_at()",
    "CREATE TABLE archive_records (\n"
    "\tid UUID NOT NULL, \n"
    "\tplan_id UUID NOT NULL, \n"
    "\tresource_kind VARCHAR(30) NOT NULL, \n"
    "\tresource_id UUID NOT NULL, \n"
    "\towner_id UUID, \n"
    "\tstatus VARCHAR(20) NOT NULL, \n"
    "\tsha256 VARCHAR(64) NOT NULL, \n"
    "\tarchive_bytes INTEGER NOT NULL, \n"
    "\treleased_file_bytes INTEGER NOT NULL, \n"
    "\tdatabase_reusable_bytes INTEGER NOT NULL, \n"
    "\tmetadata_snapshot JSON NOT NULL, \n"
    "\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n"
    "\tupdated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n"
    "\tdeleted_at TIMESTAMP WITH TIME ZONE, \n"
    "\tCONSTRAINT pk_archive_records PRIMARY KEY (id), \n"
    "\tCONSTRAINT uq_archive_records_resource_kind UNIQUE (resource_kind, resource_id), \n"
    "\tCONSTRAINT fk_archive_records_plan_id_retention_plans FOREIGN KEY(plan_id) "
    "REFERENCES retention_plans (id), \n"
    "\tCONSTRAINT fk_archive_records_owner_id_users FOREIGN KEY(owner_id) REFERENCES users "
    "(id)\n"
    ")",
    "CREATE INDEX ix_archive_records_plan_id ON archive_records (plan_id)",
    "CREATE INDEX ix_archive_records_resource_id ON archive_records (resource_id)",
    "CREATE TRIGGER trg_archive_records_updated_at BEFORE UPDATE ON archive_records FOR "
    "EACH ROW EXECUTE FUNCTION set_updated_at()",
]


def upgrade():
    op.add_column(
        "service_objectives",
        sa.Column("metric_source", sa.String(20), nullable=False, server_default="application"),
    )
    for statement in STATEMENTS:
        op.execute(statement)
    op.execute("""
        CREATE FUNCTION guard_archived_reference() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF current_setting('sentinelops.retention_writer',true) = 'on' THEN
                RETURN NEW;
            END IF;
            PERFORM pg_advisory_xact_lock(100031);
            IF EXISTS (
                SELECT 1 FROM archive_records archived
                WHERE archived.status IN ('releasing','complete')
                  AND to_jsonb(NEW)::text LIKE '%' || archived.resource_id::text || '%'
            ) THEN
                RAISE EXCEPTION 'Archived resources cannot acquire current evidence references'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END $$
    """)
    for table in GUARDED_TABLES:
        op.execute(
            f"CREATE TRIGGER trg_{table}_archive_reference BEFORE INSERT OR UPDATE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION guard_archived_reference()"
        )


def downgrade():
    op.drop_column("service_objectives", "metric_source")
    for table in GUARDED_TABLES:
        op.execute(f"DROP TRIGGER trg_{table}_archive_reference ON {table}")
    op.execute("DROP FUNCTION guard_archived_reference()")
    op.drop_table("usage_budgets")
    op.drop_table("model_prices")
    op.drop_table("usage_cursors")
    op.drop_table("model_usage")
    op.drop_table("archive_records")
    op.drop_table("retention_plans")


GUARDED_TABLES = (
    "application_releases",
    "agent_analyses",
    "analysis_runs",
    "analysis_messages",
    "analysis_events",
    "alert_events",
    "postmortem_reports",
    "postmortem_revisions",
    "repair_handoffs",
    "approvals",
    "skill_executions",
    "evaluation_cases",
    "incident_timeline",
    "improvement_tasks",
)
