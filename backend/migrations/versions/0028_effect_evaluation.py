"""Frozen table DDL; UTC timestamps and update triggers included."""

from alembic import op

revision = "0028_effect_evaluation"
down_revision = "0027_incident_response"
branch_labels = None
depends_on = None

STATEMENTS = [
    "CREATE TABLE diagnosis_reviews (\n"
    "\tid UUID NOT NULL, \n"
    "\tanalysis_id UUID NOT NULL, \n"
    "\treviewer_id UUID NOT NULL, \n"
    "\tverdict VARCHAR(20) NOT NULL, \n"
    "\tconfirmed_cause VARCHAR(4000) NOT NULL, \n"
    "\tevidence VARCHAR(8000) NOT NULL, \n"
    "\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n"
    "\tupdated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n"
    "\tdeleted_at TIMESTAMP WITH TIME ZONE, \n"
    "\tCONSTRAINT pk_diagnosis_reviews PRIMARY KEY (id), \n"
    "\tCONSTRAINT fk_diagnosis_reviews_analysis_id_agent_analyses FOREIGN KEY(analysis_id) "
    "REFERENCES agent_analyses (id), \n"
    "\tCONSTRAINT fk_diagnosis_reviews_reviewer_id_users FOREIGN KEY(reviewer_id) "
    "REFERENCES users (id)\n"
    ")",
    "CREATE INDEX ix_diagnosis_reviews_analysis_id ON diagnosis_reviews (analysis_id)",
    "CREATE TRIGGER trg_diagnosis_reviews_updated_at BEFORE UPDATE ON diagnosis_reviews FOR "
    "EACH ROW EXECUTE FUNCTION set_updated_at()",
    "CREATE TABLE evaluation_cases (\n"
    "\tid UUID NOT NULL, \n"
    "\tfamily_id UUID NOT NULL, \n"
    "\tversion INTEGER NOT NULL, \n"
    "\ttitle VARCHAR(200) NOT NULL, \n"
    "\tanalysis_id UUID NOT NULL, \n"
    "\tcreated_by UUID NOT NULL, \n"
    "\tsnapshot JSON NOT NULL, \n"
    "\tcontent_hash VARCHAR(64) NOT NULL, \n"
    "\tchecks JSON NOT NULL, \n"
    "\thuman_label VARCHAR(4000) NOT NULL, \n"
    "\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n"
    "\tupdated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n"
    "\tdeleted_at TIMESTAMP WITH TIME ZONE, \n"
    "\tCONSTRAINT pk_evaluation_cases PRIMARY KEY (id), \n"
    "\tCONSTRAINT uq_evaluation_cases_family_id UNIQUE (family_id, version), \n"
    "\tCONSTRAINT fk_evaluation_cases_analysis_id_agent_analyses FOREIGN KEY(analysis_id) "
    "REFERENCES agent_analyses (id), \n"
    "\tCONSTRAINT fk_evaluation_cases_created_by_users FOREIGN KEY(created_by) REFERENCES "
    "users (id)\n"
    ")",
    "CREATE TRIGGER trg_evaluation_cases_updated_at BEFORE UPDATE ON evaluation_cases FOR "
    "EACH ROW EXECUTE FUNCTION set_updated_at()",
    "CREATE TABLE evaluation_runs (\n"
    "\tid UUID NOT NULL, \n"
    "\tcase_id UUID NOT NULL, \n"
    "\trequested_by UUID NOT NULL, \n"
    "\tidempotency_key UUID NOT NULL, \n"
    "\trequest_hash VARCHAR(64) NOT NULL, \n"
    "\tprovider_kind VARCHAR(20) NOT NULL, \n"
    "\tstatus VARCHAR(20) NOT NULL, \n"
    "\tconfig JSON NOT NULL, \n"
    "\tprompt VARCHAR(8000) NOT NULL, \n"
    "\tresult JSON NOT NULL, \n"
    "\tstarted_at TIMESTAMP WITH TIME ZONE, \n"
    "\tfinished_at TIMESTAMP WITH TIME ZONE, \n"
    "\terror_code VARCHAR(80), \n"
    "\treview JSON NOT NULL, \n"
    "\tversion INTEGER NOT NULL, \n"
    "\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n"
    "\tupdated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n"
    "\tdeleted_at TIMESTAMP WITH TIME ZONE, \n"
    "\tCONSTRAINT pk_evaluation_runs PRIMARY KEY (id), \n"
    "\tCONSTRAINT fk_evaluation_runs_case_id_evaluation_cases FOREIGN KEY(case_id) "
    "REFERENCES evaluation_cases (id), \n"
    "\tCONSTRAINT fk_evaluation_runs_requested_by_users FOREIGN KEY(requested_by) "
    "REFERENCES users (id), \n"
    "\tCONSTRAINT uq_evaluation_runs_idempotency_key UNIQUE (idempotency_key)\n"
    ")",
    "CREATE INDEX ix_evaluation_runs_case_id ON evaluation_runs (case_id)",
    "CREATE INDEX ix_evaluation_runs_status ON evaluation_runs (status)",
    "CREATE TRIGGER trg_evaluation_runs_updated_at BEFORE UPDATE ON evaluation_runs FOR "
    "EACH ROW EXECUTE FUNCTION set_updated_at()",
    "CREATE TABLE prediction_outcomes (\n"
    "\tid UUID NOT NULL, \n"
    "\tresult_id UUID NOT NULL, \n"
    "\thorizon_hours INTEGER NOT NULL, \n"
    "\tstatus VARCHAR(20) NOT NULL, \n"
    "\tobservation JSON NOT NULL, \n"
    "\tincident_links JSON NOT NULL, \n"
    "\tversion INTEGER NOT NULL, \n"
    "\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n"
    "\tupdated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n"
    "\tdeleted_at TIMESTAMP WITH TIME ZONE, \n"
    "\tCONSTRAINT pk_prediction_outcomes PRIMARY KEY (id), \n"
    "\tCONSTRAINT uq_prediction_outcomes_result_id UNIQUE (result_id, horizon_hours), \n"
    "\tCONSTRAINT fk_prediction_outcomes_result_id_prediction_results FOREIGN "
    "KEY(result_id) REFERENCES prediction_results (id)\n"
    ")",
    "CREATE INDEX ix_prediction_outcomes_result_id ON prediction_outcomes (result_id)",
    "CREATE TRIGGER trg_prediction_outcomes_updated_at BEFORE UPDATE ON prediction_outcomes "
    "FOR EACH ROW EXECUTE FUNCTION set_updated_at()",
]


def upgrade():
    for statement in STATEMENTS:
        op.execute(statement)


def downgrade():
    op.drop_table("prediction_outcomes")
    op.drop_table("evaluation_runs")
    op.drop_table("evaluation_cases")
    op.drop_table("diagnosis_reviews")
