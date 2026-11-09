"""Platform-owned, version-isolated ontology storage and ask-data permissions."""

import sqlalchemy as sa
from alembic import op

revision = "0044_askdata_foundation"
down_revision = "0043_llm_agent_channel"
branch_labels = None
depends_on = None

# Frozen at implementation time. Never create schema from live vendor metadata.
DDL = [
    (
        "CREATE TABLE askdata.configurations (\n"
        "\tid UUID NOT NULL, \n"
        "\tconnection_id UUID NOT NULL, \n"
        "\tenabled BOOLEAN NOT NULL, \n"
        "\tversion INTEGER NOT NULL, \n"
        "\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n"
        "\tupdated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n"
        "\tdeleted_at TIMESTAMP WITH TIME ZONE, \n"
        "\tCONSTRAINT pk_configurations PRIMARY KEY (id), \n"
        "\tCONSTRAINT uq_configurations_connection_id UNIQUE (connection_id), "
        "\n"
        "\tCONSTRAINT fk_configurations_connection_id_service_databases FOREIG"
        "N KEY(connection_id) REFERENCES service_databases (id)\n"
        ")"
    ),
    (
        "CREATE TABLE askdata.conversations (\n"
        "\tid UUID NOT NULL, \n"
        "\tconnection_id UUID NOT NULL, \n"
        "\towner_id UUID NOT NULL, \n"
        "\ttitle VARCHAR(200) NOT NULL, \n"
        "\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n"
        "\tupdated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n"
        "\tdeleted_at TIMESTAMP WITH TIME ZONE, \n"
        "\tCONSTRAINT pk_conversations PRIMARY KEY (id), \n"
        "\tCONSTRAINT uq_conversations_connection_id UNIQUE (connection_id, id"
        "), \n"
        "\tCONSTRAINT fk_conversations_connection_id_service_databases FOREIGN"
        " KEY(connection_id) REFERENCES service_databases (id), \n"
        "\tCONSTRAINT fk_conversations_owner_id_users FOREIGN KEY(owner_id) RE"
        "FERENCES users (id)\n"
        ")"
    ),
    (
        "CREATE TABLE askdata.model_versions (\n"
        "\tid UUID NOT NULL, \n"
        "\tconnection_id UUID NOT NULL, \n"
        "\tsource_snapshot_id UUID, \n"
        "\tcreated_by UUID NOT NULL, \n"
        "\tconfig_version INTEGER NOT NULL, \n"
        "\tschema_hash VARCHAR(64) NOT NULL, \n"
        "\tstatus VARCHAR(20) NOT NULL, \n"
        "\tcontext JSON NOT NULL, \n"
        "\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n"
        "\tupdated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n"
        "\tdeleted_at TIMESTAMP WITH TIME ZONE, \n"
        "\tCONSTRAINT pk_model_versions PRIMARY KEY (id), \n"
        "\tCONSTRAINT uq_model_versions_connection_id UNIQUE (connection_id, i"
        "d), \n"
        "\tCONSTRAINT ck_model_versions_status CHECK (status IN ('draft','read"
        "y','failed','retired')), \n"
        "\tCONSTRAINT fk_model_versions_connection_id_service_databases FOREIG"
        "N KEY(connection_id) REFERENCES service_databases (id), \n"
        "\tCONSTRAINT fk_model_versions_source_snapshot_id_source_snapshots FO"
        "REIGN KEY(source_snapshot_id) REFERENCES source_snapshots (id), \n"
        "\tCONSTRAINT fk_model_versions_created_by_users FOREIGN KEY(created_b"
        "y) REFERENCES users (id)\n"
        ")"
    ),
    (
        "CREATE TABLE askdata.analysis (\n"
        "\tid TEXT DEFAULT (gen_random_uuid())::text NOT NULL, \n"
        "\tname TEXT NOT NULL, \n"
        "\tdescription TEXT, \n"
        "\timported_id TEXT, \n"
        "\tmodel_version_id UUID NOT NULL, \n"
        "\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n"
        "\tupdated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n"
        "\tdeleted_at TIMESTAMP WITH TIME ZONE, \n"
        "\tCONSTRAINT pk_analysis PRIMARY KEY (model_version_id, id), \n"
        "\tCONSTRAINT fk_analysis_model_version_id_model_versions FOREIGN KEY("
        "model_version_id) REFERENCES askdata.model_versions (id)\n"
        ")"
    ),
    (
        "CREATE TABLE askdata.builds (\n"
        "\tid UUID NOT NULL, \n"
        "\tconnection_id UUID NOT NULL, \n"
        "\tmodel_version_id UUID NOT NULL, \n"
        "\trequested_by UUID NOT NULL, \n"
        "\tstatus VARCHAR(20) NOT NULL, \n"
        "\tstep VARCHAR(100) NOT NULL, \n"
        "\terror_code VARCHAR(100), \n"
        "\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n"
        "\tupdated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n"
        "\tdeleted_at TIMESTAMP WITH TIME ZONE, \n"
        "\tCONSTRAINT pk_builds PRIMARY KEY (id), \n"
        "\tCONSTRAINT fk_builds_connection_id_model_versions FOREIGN KEY(conne"
        "ction_id, model_version_id) REFERENCES askdata.model_versions (conne"
        "ction_id, id), \n"
        "\tCONSTRAINT ck_builds_status CHECK (status IN ('queued','running','c"
        "ompleted','failed','cancelled','interrupted')), \n"
        "\tCONSTRAINT fk_builds_connection_id_service_databases FOREIGN KEY(co"
        "nnection_id) REFERENCES service_databases (id), \n"
        "\tCONSTRAINT fk_builds_requested_by_users FOREIGN KEY(requested_by) R"
        "EFERENCES users (id)\n"
        ")"
    ),
    (
        "CREATE TABLE askdata.catalog_database (\n"
        "\tid TEXT DEFAULT (gen_random_uuid())::text NOT NULL, \n"
        "\tname TEXT NOT NULL, \n"
        "\tconnection JSONB, \n"
        "\tdialect TEXT, \n"
        "\tdescription TEXT, \n"
        "\tdescription_certified BOOLEAN DEFAULT false NOT NULL, \n"
        "\timported_id TEXT, \n"
        "\towner_id TEXT, \n"
        "\tcreated TIMESTAMP WITH TIME ZONE DEFAULT now(), \n"
        "\tpulled TIMESTAMP WITH TIME ZONE, \n"
        "\tmodel_version_id UUID NOT NULL, \n"
        "\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n"
        "\tupdated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n"
        "\tdeleted_at TIMESTAMP WITH TIME ZONE, \n"
        "\tCONSTRAINT pk_catalog_database PRIMARY KEY (model_version_id, id), "
        "\n"
        "\tCONSTRAINT uq_catalog_database_name UNIQUE (model_version_id, name)"
        ", \n"
        "\tCONSTRAINT fk_catalog_database_model_version_id_model_versions FORE"
        "IGN KEY(model_version_id) REFERENCES askdata.model_versions (id), \n"
        "\tCONSTRAINT ck_catalog_database_no_credentials CHECK (connection IS "
        "NULL)\n"
        ")"
    ),
    (
        "CREATE TABLE askdata.column_attribute (\n"
        "\tid TEXT DEFAULT (gen_random_uuid())::text NOT NULL, \n"
        "\tname TEXT NOT NULL, \n"
        "\tdescription TEXT, \n"
        "\tdatatype TEXT, \n"
        "\tsource TEXT DEFAULT 'semantic' NOT NULL, \n"
        "\tsource_column TEXT NOT NULL, \n"
        "\tterm_name TEXT NOT NULL, \n"
        "\ttable_id TEXT NOT NULL, \n"
        "\tcertified BOOLEAN DEFAULT false NOT NULL, \n"
        "\tdescription_suggestion TEXT, \n"
        "\tpii_processed BOOLEAN DEFAULT false NOT NULL, \n"
        "\tcreated TIMESTAMP WITH TIME ZONE DEFAULT now(), \n"
        "\tmodified TIMESTAMP WITH TIME ZONE, \n"
        "\tcreated_by TEXT, \n"
        "\tmodified_by TEXT, \n"
        "\timported_id TEXT, \n"
        "\tmodel_version_id UUID NOT NULL, \n"
        "\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n"
        "\tupdated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n"
        "\tdeleted_at TIMESTAMP WITH TIME ZONE, \n"
        "\tCONSTRAINT pk_column_attribute PRIMARY KEY (model_version_id, id), "
        "\n"
        "\tCONSTRAINT uq_column_attribute_merge_key UNIQUE (model_version_id, "
        "name, source_column, term_name, table_id, source), \n"
        "\tCONSTRAINT fk_column_attribute_model_version_id_model_versions FORE"
        "IGN KEY(model_version_id) REFERENCES askdata.model_versions (id)\n"
        ")"
    ),
    (
        "CREATE TABLE askdata.custom_analysis (\n"
        "\tid TEXT DEFAULT (gen_random_uuid())::text NOT NULL, \n"
        "\tname TEXT NOT NULL, \n"
        "\tdescription TEXT, \n"
        "\timported_id TEXT, \n"
        "\tmodel_version_id UUID NOT NULL, \n"
        "\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n"
        "\tupdated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n"
        "\tdeleted_at TIMESTAMP WITH TIME ZONE, \n"
        "\tCONSTRAINT pk_custom_analysis PRIMARY KEY (model_version_id, id), \n"
        "\tCONSTRAINT uq_custom_analysis_name UNIQUE (model_version_id, name),"
        " \n"
        "\tCONSTRAINT fk_custom_analysis_model_version_id_model_versions FOREI"
        "GN KEY(model_version_id) REFERENCES askdata.model_versions (id)\n"
        ")"
    ),
    (
        "CREATE TABLE askdata.rule (\n"
        "\tid TEXT DEFAULT (gen_random_uuid())::text NOT NULL, \n"
        "\tname TEXT NOT NULL, \n"
        "\tsearch_term TEXT NOT NULL, \n"
        "\ttext_match_option TEXT DEFAULT 'contains' NOT NULL, \n"
        "\tfilters JSONB DEFAULT '{}'::jsonb NOT NULL, \n"
        "\tcreated TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n"
        "\tmodified TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n"
        "\tcreated_by TEXT NOT NULL, \n"
        "\tmodified_by TEXT, \n"
        "\tmodel_version_id UUID NOT NULL, \n"
        "\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n"
        "\tupdated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n"
        "\tdeleted_at TIMESTAMP WITH TIME ZONE, \n"
        "\tCONSTRAINT pk_rule PRIMARY KEY (model_version_id, id), \n"
        "\tCONSTRAINT fk_rule_model_version_id_model_versions FOREIGN KEY(mode"
        "l_version_id) REFERENCES askdata.model_versions (id)\n"
        ")"
    ),
    (
        "CREATE TABLE askdata.runs (\n"
        "\tid UUID NOT NULL, \n"
        "\tconnection_id UUID NOT NULL, \n"
        "\tconversation_id UUID NOT NULL, \n"
        "\tmodel_version_id UUID NOT NULL, \n"
        "\tidempotency_key UUID NOT NULL, \n"
        "\tstatus VARCHAR(30) NOT NULL, \n"
        "\tquestion TEXT NOT NULL, \n"
        "\tresult JSON NOT NULL, \n"
        "\terror_code VARCHAR(100), \n"
        "\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n"
        "\tupdated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n"
        "\tdeleted_at TIMESTAMP WITH TIME ZONE, \n"
        "\tCONSTRAINT pk_runs PRIMARY KEY (id), \n"
        "\tCONSTRAINT fk_runs_connection_id_conversations FOREIGN KEY(connecti"
        "on_id, conversation_id) REFERENCES askdata.conversations (connection"
        "_id, id), \n"
        "\tCONSTRAINT fk_runs_connection_id_model_versions FOREIGN KEY(connect"
        "ion_id, model_version_id) REFERENCES askdata.model_versions (connect"
        "ion_id, id), \n"
        "\tCONSTRAINT uq_runs_conversation_id UNIQUE (conversation_id, idempot"
        "ency_key), \n"
        "\tCONSTRAINT ck_runs_status CHECK (status IN ('queued','running','com"
        "pleted','needs_clarification','failed','cancelled','interrupted')), "
        "\n"
        "\tCONSTRAINT fk_runs_connection_id_service_databases FOREIGN KEY(conn"
        "ection_id) REFERENCES service_databases (id)\n"
        ")"
    ),
    (
        "CREATE TABLE askdata.semantic_compilation_history (\n"
        "\tid BIGSERIAL NOT NULL, \n"
        "\tstarted_at TIMESTAMP WITH TIME ZONE NOT NULL, \n"
        "\tfinished_at TIMESTAMP WITH TIME ZONE, \n"
        "\tstatus TEXT, \n"
        "\tmodel_version_id UUID NOT NULL, \n"
        "\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n"
        "\tupdated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n"
        "\tdeleted_at TIMESTAMP WITH TIME ZONE, \n"
        "\tCONSTRAINT pk_semantic_compilation_history PRIMARY KEY (model_versi"
        "on_id, id), \n"
        "\tCONSTRAINT ck_semantic_compilation_history_status_value CHECK (stat"
        "us IS NULL OR status IN ('succeeded', 'failed')), \n"
        "\tCONSTRAINT fk_semantic_compilation_history_model_version_id_model_v"
        "ersions FOREIGN KEY(model_version_id) REFERENCES askdata.model_versi"
        "ons (id)\n"
        ")"
    ),
    (
        "CREATE TABLE askdata.sql_attribute (\n"
        "\tid TEXT DEFAULT (gen_random_uuid())::text NOT NULL, \n"
        "\tname TEXT NOT NULL, \n"
        "\tdescription TEXT, \n"
        "\texpression TEXT, \n"
        "\tsource TEXT, \n"
        "\tcertified BOOLEAN DEFAULT false NOT NULL, \n"
        "\towner_id TEXT, \n"
        "\tdescription_suggestion TEXT, \n"
        "\tpii_processed BOOLEAN DEFAULT false NOT NULL, \n"
        "\tcreated TIMESTAMP WITH TIME ZONE DEFAULT now(), \n"
        "\tmodified TIMESTAMP WITH TIME ZONE, \n"
        "\tcreated_by TEXT, \n"
        "\tmodified_by TEXT, \n"
        "\timported_id TEXT, \n"
        "\tmodel_version_id UUID NOT NULL, \n"
        "\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n"
        "\tupdated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n"
        "\tdeleted_at TIMESTAMP WITH TIME ZONE, \n"
        "\tCONSTRAINT pk_sql_attribute PRIMARY KEY (model_version_id, id), \n"
        "\tCONSTRAINT ck_sql_attribute_source_value CHECK (source IS NULL OR s"
        "ource IN ('manual', 'sql', 'table', 'bridgeTable')), \n"
        "\tCONSTRAINT uq_sql_attribute_name UNIQUE (model_version_id, name), \n"
        "\tCONSTRAINT fk_sql_attribute_model_version_id_model_versions FOREIGN"
        " KEY(model_version_id) REFERENCES askdata.model_versions (id)\n"
        ")"
    ),
    (
        "CREATE TABLE askdata.sql_query (\n"
        "\tid TEXT DEFAULT (gen_random_uuid())::text NOT NULL, \n"
        "\tsql_full_query TEXT NOT NULL, \n"
        "\tquery_count INTEGER DEFAULT 0 NOT NULL, \n"
        "\tcreated TIMESTAMP WITH TIME ZONE DEFAULT now(), \n"
        "\tlast_seen TIMESTAMP WITH TIME ZONE, \n"
        "\ttotal_counter INTEGER DEFAULT 0 NOT NULL, \n"
        "\tlast_query_timestamp TIMESTAMP WITH TIME ZONE, \n"
        "\tnodes_count INTEGER, \n"
        "\tjoin_count INTEGER DEFAULT 0 NOT NULL, \n"
        "\tunion_count INTEGER DEFAULT 0 NOT NULL, \n"
        "\tmodel_version_id UUID NOT NULL, \n"
        "\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n"
        "\tupdated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n"
        "\tdeleted_at TIMESTAMP WITH TIME ZONE, \n"
        "\tCONSTRAINT pk_sql_query PRIMARY KEY (model_version_id, id), \n"
        "\tCONSTRAINT fk_sql_query_model_version_id_model_versions FOREIGN KEY"
        "(model_version_id) REFERENCES askdata.model_versions (id)\n"
        ")"
    ),
    (
        "CREATE TABLE askdata.tag (\n"
        "\tid TEXT DEFAULT (gen_random_uuid())::text NOT NULL, \n"
        "\tname TEXT NOT NULL, \n"
        "\tcreated TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n"
        "\tmodified TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n"
        "\tcreated_by TEXT, \n"
        "\tmodified_by TEXT, \n"
        "\tmodel_version_id UUID NOT NULL, \n"
        "\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n"
        "\tupdated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n"
        "\tdeleted_at TIMESTAMP WITH TIME ZONE, \n"
        "\tCONSTRAINT pk_tag PRIMARY KEY (model_version_id, id), \n"
        "\tCONSTRAINT fk_tag_model_version_id_model_versions FOREIGN KEY(model"
        "_version_id) REFERENCES askdata.model_versions (id)\n"
        ")"
    ),
    (
        "CREATE TABLE askdata.term (\n"
        "\tid TEXT DEFAULT (gen_random_uuid())::text NOT NULL, \n"
        "\tname TEXT NOT NULL, \n"
        "\tdescription TEXT, \n"
        "\tsource TEXT DEFAULT 'semantic' NOT NULL, \n"
        "\tsynonyms TEXT[], \n"
        "\tname_certified BOOLEAN DEFAULT false NOT NULL, \n"
        "\tdescription_certified BOOLEAN DEFAULT false NOT NULL, \n"
        "\towner_id TEXT, \n"
        "\tcreated TIMESTAMP WITH TIME ZONE DEFAULT now(), \n"
        "\tmodified TIMESTAMP WITH TIME ZONE, \n"
        "\tcreated_by TEXT, \n"
        "\tmodified_by TEXT, \n"
        "\timported_id TEXT, \n"
        "\tmodel_version_id UUID NOT NULL, \n"
        "\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n"
        "\tupdated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n"
        "\tdeleted_at TIMESTAMP WITH TIME ZONE, \n"
        "\tCONSTRAINT pk_term PRIMARY KEY (model_version_id, id), \n"
        "\tCONSTRAINT uq_term_name_source UNIQUE (model_version_id, name, sour"
        "ce), \n"
        "\tCONSTRAINT fk_term_model_version_id_model_versions FOREIGN KEY(mode"
        "l_version_id) REFERENCES askdata.model_versions (id)\n"
        ")"
    ),
    (
        "CREATE TABLE askdata.text_attribute (\n"
        "\tid TEXT DEFAULT (gen_random_uuid())::text NOT NULL, \n"
        "\tname TEXT NOT NULL, \n"
        "\tdescription TEXT, \n"
        "\timported_id TEXT, \n"
        "\tmodel_version_id UUID NOT NULL, \n"
        "\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n"
        "\tupdated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n"
        "\tdeleted_at TIMESTAMP WITH TIME ZONE, \n"
        "\tCONSTRAINT pk_text_attribute PRIMARY KEY (model_version_id, id), \n"
        "\tCONSTRAINT fk_text_attribute_model_version_id_model_versions FOREIG"
        "N KEY(model_version_id) REFERENCES askdata.model_versions (id)\n"
        ")"
    ),
    (
        "CREATE TABLE askdata.zone (\n"
        "\tid TEXT DEFAULT (gen_random_uuid())::text NOT NULL, \n"
        "\tname TEXT NOT NULL, \n"
        "\tdescription TEXT, \n"
        "\tcolor TEXT, \n"
        "\tenabled BOOLEAN DEFAULT true NOT NULL, \n"
        "\tmodel_version_id UUID NOT NULL, \n"
        "\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n"
        "\tupdated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n"
        "\tdeleted_at TIMESTAMP WITH TIME ZONE, \n"
        "\tCONSTRAINT pk_zone PRIMARY KEY (model_version_id, id), \n"
        "\tCONSTRAINT fk_zone_model_version_id_model_versions FOREIGN KEY(mode"
        "l_version_id) REFERENCES askdata.model_versions (id)\n"
        ")"
    ),
    (
        "CREATE TABLE askdata.catalog_schema (\n"
        "\tid TEXT DEFAULT (gen_random_uuid())::text NOT NULL, \n"
        "\tdatabase_id TEXT NOT NULL, \n"
        "\tname TEXT NOT NULL, \n"
        "\tdescription TEXT, \n"
        "\tdescription_certified BOOLEAN DEFAULT false NOT NULL, \n"
        "\timported_id TEXT, \n"
        "\towner_id TEXT, \n"
        "\tmodel_version_id UUID NOT NULL, \n"
        "\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n"
        "\tupdated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n"
        "\tdeleted_at TIMESTAMP WITH TIME ZONE, \n"
        "\tCONSTRAINT pk_catalog_schema PRIMARY KEY (model_version_id, id), \n"
        "\tCONSTRAINT uq_catalog_schema_database_name UNIQUE (model_version_id"
        ", database_id, name), \n"
        "\tCONSTRAINT fk_catalog_schema_database_id_catalog_database FOREIGN K"
        "EY(model_version_id, database_id) REFERENCES askdata.catalog_databas"
        "e (model_version_id, id), \n"
        "\tCONSTRAINT fk_catalog_schema_model_version_id_model_versions FOREIG"
        "N KEY(model_version_id) REFERENCES askdata.model_versions (id)\n"
        ")"
    ),
    (
        "CREATE TABLE askdata.column_attribute__term (\n"
        "\tattribute_id TEXT NOT NULL, \n"
        "\tterm_id TEXT NOT NULL, \n"
        "\tmodel_version_id UUID NOT NULL, \n"
        "\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n"
        "\tupdated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n"
        "\tdeleted_at TIMESTAMP WITH TIME ZONE, \n"
        "\tCONSTRAINT pk_column_attribute__term PRIMARY KEY (model_version_id,"
        " attribute_id, term_id), \n"
        "\tCONSTRAINT fk_column_attribute__term_term_id_term FOREIGN KEY(model"
        "_version_id, term_id) REFERENCES askdata.term (model_version_id, id)"
        ", \n"
        "\tCONSTRAINT fk_column_attribute__term_attribute_id_column_attribute "
        "FOREIGN KEY(model_version_id, attribute_id) REFERENCES askdata.colum"
        "n_attribute (model_version_id, id), \n"
        "\tCONSTRAINT uq_column_attribute__term_attribute_id UNIQUE (model_ver"
        "sion_id, attribute_id), \n"
        "\tCONSTRAINT fk_column_attribute__term_model_version_id_model_version"
        "s FOREIGN KEY(model_version_id) REFERENCES askdata.model_versions (i"
        "d)\n"
        ")"
    ),
    (
        "CREATE TABLE askdata.custom_analysis__sql (\n"
        "\tanalysis_id TEXT NOT NULL, \n"
        "\tsql_query_id TEXT NOT NULL, \n"
        "\tmodel_version_id UUID NOT NULL, \n"
        "\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n"
        "\tupdated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n"
        "\tdeleted_at TIMESTAMP WITH TIME ZONE, \n"
        "\tCONSTRAINT pk_custom_analysis__sql PRIMARY KEY (model_version_id, a"
        "nalysis_id, sql_query_id), \n"
        "\tCONSTRAINT fk_custom_analysis__sql_analysis_id_custom_analysis FORE"
        "IGN KEY(model_version_id, analysis_id) REFERENCES askdata.custom_ana"
        "lysis (model_version_id, id), \n"
        "\tCONSTRAINT fk_custom_analysis__sql_sql_query_id_sql_query FOREIGN K"
        "EY(model_version_id, sql_query_id) REFERENCES askdata.sql_query (mod"
        "el_version_id, id), \n"
        "\tCONSTRAINT fk_custom_analysis__sql_model_version_id_model_versions "
        "FOREIGN KEY(model_version_id) REFERENCES askdata.model_versions (id)"
        "\n"
        ")"
    ),
    (
        "CREATE TABLE askdata.events (\n"
        "\tid UUID NOT NULL, \n"
        "\trun_id UUID NOT NULL, \n"
        "\tseq INTEGER NOT NULL, \n"
        "\tkind VARCHAR(50) NOT NULL, \n"
        "\tdata JSON NOT NULL, \n"
        "\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n"
        "\tupdated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n"
        "\tdeleted_at TIMESTAMP WITH TIME ZONE, \n"
        "\tCONSTRAINT pk_events PRIMARY KEY (id), \n"
        "\tCONSTRAINT uq_events_run_id UNIQUE (run_id, seq), \n"
        "\tCONSTRAINT fk_events_run_id_runs FOREIGN KEY(run_id) REFERENCES ask"
        "data.runs (id)\n"
        ")"
    ),
    (
        "CREATE TABLE askdata.rule__tag (\n"
        "\trule_id TEXT NOT NULL, \n"
        "\ttag_id TEXT NOT NULL, \n"
        "\tposition INTEGER NOT NULL, \n"
        "\tmodel_version_id UUID NOT NULL, \n"
        "\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n"
        "\tupdated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n"
        "\tdeleted_at TIMESTAMP WITH TIME ZONE, \n"
        "\tCONSTRAINT pk_rule__tag PRIMARY KEY (model_version_id, rule_id, tag"
        "_id), \n"
        "\tCONSTRAINT fk_rule__tag_tag_id_tag FOREIGN KEY(model_version_id, ta"
        "g_id) REFERENCES askdata.tag (model_version_id, id), \n"
        "\tCONSTRAINT fk_rule__tag_rule_id_rule FOREIGN KEY(model_version_id, "
        "rule_id) REFERENCES askdata.rule (model_version_id, id), \n"
        "\tCONSTRAINT fk_rule__tag_model_version_id_model_versions FOREIGN KEY"
        "(model_version_id) REFERENCES askdata.model_versions (id)\n"
        ")"
    ),
    (
        "CREATE TABLE askdata.sql_attribute__sql (\n"
        "\tattribute_id TEXT NOT NULL, \n"
        "\tsql_query_id TEXT NOT NULL, \n"
        "\tmodel_version_id UUID NOT NULL, \n"
        "\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n"
        "\tupdated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n"
        "\tdeleted_at TIMESTAMP WITH TIME ZONE, \n"
        "\tCONSTRAINT pk_sql_attribute__sql PRIMARY KEY (model_version_id, att"
        "ribute_id, sql_query_id), \n"
        "\tCONSTRAINT fk_sql_attribute__sql_sql_query_id_sql_query FOREIGN KEY"
        "(model_version_id, sql_query_id) REFERENCES askdata.sql_query (model"
        "_version_id, id), \n"
        "\tCONSTRAINT fk_sql_attribute__sql_attribute_id_sql_attribute FOREIGN"
        " KEY(model_version_id, attribute_id) REFERENCES askdata.sql_attribut"
        "e (model_version_id, id), \n"
        "\tCONSTRAINT fk_sql_attribute__sql_model_version_id_model_versions FO"
        "REIGN KEY(model_version_id) REFERENCES askdata.model_versions (id)\n"
        ")"
    ),
    (
        "CREATE TABLE askdata.sql_attribute__term (\n"
        "\tattribute_id TEXT NOT NULL, \n"
        "\tterm_id TEXT NOT NULL, \n"
        "\tmodel_version_id UUID NOT NULL, \n"
        "\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n"
        "\tupdated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n"
        "\tdeleted_at TIMESTAMP WITH TIME ZONE, \n"
        "\tCONSTRAINT pk_sql_attribute__term PRIMARY KEY (model_version_id, at"
        "tribute_id, term_id), \n"
        "\tCONSTRAINT fk_sql_attribute__term_term_id_term FOREIGN KEY(model_ve"
        "rsion_id, term_id) REFERENCES askdata.term (model_version_id, id), \n"
        "\tCONSTRAINT uq_sql_attribute__term_attribute_id UNIQUE (model_versio"
        "n_id, attribute_id), \n"
        "\tCONSTRAINT fk_sql_attribute__term_attribute_id_sql_attribute FOREIG"
        "N KEY(model_version_id, attribute_id) REFERENCES askdata.sql_attribu"
        "te (model_version_id, id), \n"
        "\tCONSTRAINT fk_sql_attribute__term_model_version_id_model_versions F"
        "OREIGN KEY(model_version_id) REFERENCES askdata.model_versions (id)\n"
        ")"
    ),
    (
        "CREATE TABLE askdata.catalog_table (\n"
        "\tid TEXT DEFAULT (gen_random_uuid())::text NOT NULL, \n"
        "\tschema_id TEXT NOT NULL, \n"
        "\tname TEXT NOT NULL, \n"
        "\tdescription TEXT, \n"
        "\tdescription_certified BOOLEAN DEFAULT false NOT NULL, \n"
        "\towner_id TEXT, \n"
        "\tpk TEXT[], \n"
        "\ttable_type TEXT, \n"
        "\tis_junction_table BOOLEAN DEFAULT false NOT NULL, \n"
        "\timported_id TEXT, \n"
        "\tmodel_version_id UUID NOT NULL, \n"
        "\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n"
        "\tupdated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n"
        "\tdeleted_at TIMESTAMP WITH TIME ZONE, \n"
        "\tCONSTRAINT pk_catalog_table PRIMARY KEY (model_version_id, id), \n"
        "\tCONSTRAINT fk_catalog_table_schema_id_catalog_schema FOREIGN KEY(mo"
        "del_version_id, schema_id) REFERENCES askdata.catalog_schema (model_"
        "version_id, id), \n"
        "\tCONSTRAINT uq_catalog_table_schema_name UNIQUE (model_version_id, s"
        "chema_id, name), \n"
        "\tCONSTRAINT fk_catalog_table_model_version_id_model_versions FOREIGN"
        " KEY(model_version_id) REFERENCES askdata.model_versions (id)\n"
        ")"
    ),
    (
        "CREATE TABLE askdata.catalog_column (\n"
        "\tid TEXT DEFAULT (gen_random_uuid())::text NOT NULL, \n"
        "\ttable_id TEXT NOT NULL, \n"
        "\tname TEXT NOT NULL, \n"
        "\tdescription TEXT, \n"
        "\tdescription_certified BOOLEAN DEFAULT false NOT NULL, \n"
        "\tdata_type TEXT, \n"
        "\tsample_values TEXT, \n"
        "\tformat TEXT, \n"
        "\tis_unique BOOLEAN, \n"
        "\tis_nullable BOOLEAN, \n"
        "\tordinal_position INTEGER, \n"
        "\tpii_processed BOOLEAN DEFAULT false NOT NULL, \n"
        "\timported_id TEXT, \n"
        "\tmodel_version_id UUID NOT NULL, \n"
        "\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n"
        "\tupdated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n"
        "\tdeleted_at TIMESTAMP WITH TIME ZONE, \n"
        "\tCONSTRAINT pk_catalog_column PRIMARY KEY (model_version_id, id), \n"
        "\tCONSTRAINT uq_catalog_column_table_name UNIQUE (model_version_id, t"
        "able_id, name), \n"
        "\tCONSTRAINT fk_catalog_column_table_id_catalog_table FOREIGN KEY(mod"
        "el_version_id, table_id) REFERENCES askdata.catalog_table (model_ver"
        "sion_id, id), \n"
        "\tCONSTRAINT fk_catalog_column_model_version_id_model_versions FOREIG"
        "N KEY(model_version_id) REFERENCES askdata.model_versions (id)\n"
        ")"
    ),
    (
        "CREATE TABLE askdata.sql_query__table (\n"
        "\tsql_query_id TEXT NOT NULL, \n"
        "\ttable_id TEXT NOT NULL, \n"
        "\tmodel_version_id UUID NOT NULL, \n"
        "\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n"
        "\tupdated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n"
        "\tdeleted_at TIMESTAMP WITH TIME ZONE, \n"
        "\tCONSTRAINT pk_sql_query__table PRIMARY KEY (model_version_id, sql_q"
        "uery_id, table_id), \n"
        "\tCONSTRAINT fk_sql_query__table_table_id_catalog_table FOREIGN KEY(m"
        "odel_version_id, table_id) REFERENCES askdata.catalog_table (model_v"
        "ersion_id, id), \n"
        "\tCONSTRAINT fk_sql_query__table_sql_query_id_sql_query FOREIGN KEY(m"
        "odel_version_id, sql_query_id) REFERENCES askdata.sql_query (model_v"
        "ersion_id, id), \n"
        "\tCONSTRAINT fk_sql_query__table_model_version_id_model_versions FORE"
        "IGN KEY(model_version_id) REFERENCES askdata.model_versions (id)\n"
        ")"
    ),
    (
        "CREATE TABLE askdata.table__join (\n"
        "\tsource_table_id TEXT NOT NULL, \n"
        "\ttarget_table_id TEXT NOT NULL, \n"
        "\tjoin_columns JSONB, \n"
        "\tmodel_version_id UUID NOT NULL, \n"
        "\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n"
        "\tupdated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n"
        "\tdeleted_at TIMESTAMP WITH TIME ZONE, \n"
        "\tCONSTRAINT pk_table__join PRIMARY KEY (model_version_id, source_tab"
        "le_id, target_table_id), \n"
        "\tCONSTRAINT fk_table__join_target_table_id_catalog_table FOREIGN KEY"
        "(model_version_id, target_table_id) REFERENCES askdata.catalog_table"
        " (model_version_id, id), \n"
        "\tCONSTRAINT fk_table__join_source_table_id_catalog_table FOREIGN KEY"
        "(model_version_id, source_table_id) REFERENCES askdata.catalog_table"
        " (model_version_id, id), \n"
        "\tCONSTRAINT fk_table__join_model_version_id_model_versions FOREIGN K"
        "EY(model_version_id) REFERENCES askdata.model_versions (id)\n"
        ")"
    ),
    (
        "CREATE TABLE askdata.table__term (\n"
        "\ttable_id TEXT NOT NULL, \n"
        "\tterm_id TEXT NOT NULL, \n"
        "\tmodel_version_id UUID NOT NULL, \n"
        "\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n"
        "\tupdated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n"
        "\tdeleted_at TIMESTAMP WITH TIME ZONE, \n"
        "\tCONSTRAINT pk_table__term PRIMARY KEY (model_version_id, table_id, "
        "term_id), \n"
        "\tCONSTRAINT fk_table__term_term_id_term FOREIGN KEY(model_version_id"
        ", term_id) REFERENCES askdata.term (model_version_id, id), \n"
        "\tCONSTRAINT fk_table__term_table_id_catalog_table FOREIGN KEY(model_"
        "version_id, table_id) REFERENCES askdata.catalog_table (model_versio"
        "n_id, id), \n"
        "\tCONSTRAINT fk_table__term_model_version_id_model_versions FOREIGN K"
        "EY(model_version_id) REFERENCES askdata.model_versions (id)\n"
        ")"
    ),
    (
        "CREATE TABLE askdata.zone_target (\n"
        "\tid TEXT DEFAULT (gen_random_uuid())::text NOT NULL, \n"
        "\tzone_id TEXT NOT NULL, \n"
        "\tdatabase_id TEXT, \n"
        "\tschema_id TEXT, \n"
        "\ttable_id TEXT, \n"
        "\tmodel_version_id UUID NOT NULL, \n"
        "\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n"
        "\tupdated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n"
        "\tdeleted_at TIMESTAMP WITH TIME ZONE, \n"
        "\tCONSTRAINT pk_zone_target PRIMARY KEY (model_version_id, id), \n"
        "\tCONSTRAINT ck_zone_target_exactly_one_target CHECK (num_nonnulls(da"
        "tabase_id, schema_id, table_id) = 1), \n"
        "\tCONSTRAINT fk_zone_target_zone_id_zone FOREIGN KEY(model_version_id"
        ", zone_id) REFERENCES askdata.zone (model_version_id, id), \n"
        "\tCONSTRAINT fk_zone_target_database_id_catalog_database FOREIGN KEY("
        "model_version_id, database_id) REFERENCES askdata.catalog_database ("
        "model_version_id, id), \n"
        "\tCONSTRAINT fk_zone_target_table_id_catalog_table FOREIGN KEY(model_"
        "version_id, table_id) REFERENCES askdata.catalog_table (model_versio"
        "n_id, id), \n"
        "\tCONSTRAINT fk_zone_target_schema_id_catalog_schema FOREIGN KEY(mode"
        "l_version_id, schema_id) REFERENCES askdata.catalog_schema (model_ve"
        "rsion_id, id), \n"
        "\tCONSTRAINT fk_zone_target_model_version_id_model_versions FOREIGN K"
        "EY(model_version_id) REFERENCES askdata.model_versions (id)\n"
        ")"
    ),
    (
        "CREATE TABLE askdata.column__foreign_key (\n"
        "\tsource_column_id TEXT NOT NULL, \n"
        "\ttarget_column_id TEXT NOT NULL, \n"
        "\tlast_seen TIMESTAMP WITH TIME ZONE, \n"
        "\tmodel_version_id UUID NOT NULL, \n"
        "\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n"
        "\tupdated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n"
        "\tdeleted_at TIMESTAMP WITH TIME ZONE, \n"
        "\tCONSTRAINT pk_column__foreign_key PRIMARY KEY (model_version_id, so"
        "urce_column_id, target_column_id), \n"
        "\tCONSTRAINT fk_column__foreign_key_target_column_id_catalog_column F"
        "OREIGN KEY(model_version_id, target_column_id) REFERENCES askdata.ca"
        "talog_column (model_version_id, id), \n"
        "\tCONSTRAINT fk_column__foreign_key_source_column_id_catalog_column F"
        "OREIGN KEY(model_version_id, source_column_id) REFERENCES askdata.ca"
        "talog_column (model_version_id, id), \n"
        "\tCONSTRAINT fk_column__foreign_key_model_version_id_model_versions F"
        "OREIGN KEY(model_version_id) REFERENCES askdata.model_versions (id)\n"
        ")"
    ),
    (
        "CREATE TABLE askdata.column__has_attribute (\n"
        "\tcolumn_id TEXT NOT NULL, \n"
        "\tattribute_id TEXT NOT NULL, \n"
        "\tmodel_version_id UUID NOT NULL, \n"
        "\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n"
        "\tupdated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n"
        "\tdeleted_at TIMESTAMP WITH TIME ZONE, \n"
        "\tCONSTRAINT pk_column__has_attribute PRIMARY KEY (model_version_id, "
        "column_id, attribute_id), \n"
        "\tCONSTRAINT fk_column__has_attribute_attribute_id_column_attribute F"
        "OREIGN KEY(model_version_id, attribute_id) REFERENCES askdata.column"
        "_attribute (model_version_id, id), \n"
        "\tCONSTRAINT fk_column__has_attribute_column_id_catalog_column FOREIG"
        "N KEY(model_version_id, column_id) REFERENCES askdata.catalog_column"
        " (model_version_id, id), \n"
        "\tCONSTRAINT fk_column__has_attribute_model_version_id_model_versions"
        " FOREIGN KEY(model_version_id) REFERENCES askdata.model_versions (id"
        ")\n"
        ")"
    ),
    (
        "CREATE TABLE askdata.column__join (\n"
        "\tsource_column_id TEXT NOT NULL, \n"
        "\ttarget_column_id TEXT NOT NULL, \n"
        "\trefs TEXT[] DEFAULT '{}' NOT NULL, \n"
        "\tmodel_version_id UUID NOT NULL, \n"
        "\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n"
        "\tupdated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n"
        "\tdeleted_at TIMESTAMP WITH TIME ZONE, \n"
        "\tCONSTRAINT pk_column__join PRIMARY KEY (model_version_id, source_co"
        "lumn_id, target_column_id), \n"
        "\tCONSTRAINT fk_column__join_target_column_id_catalog_column FOREIGN "
        "KEY(model_version_id, target_column_id) REFERENCES askdata.catalog_c"
        "olumn (model_version_id, id), \n"
        "\tCONSTRAINT fk_column__join_source_column_id_catalog_column FOREIGN "
        "KEY(model_version_id, source_column_id) REFERENCES askdata.catalog_c"
        "olumn (model_version_id, id), \n"
        "\tCONSTRAINT fk_column__join_model_version_id_model_versions FOREIGN "
        "KEY(model_version_id) REFERENCES askdata.model_versions (id)\n"
        ")"
    ),
    (
        "CREATE TABLE askdata.column__semantic_fk (\n"
        "\tcolumn_id TEXT NOT NULL, \n"
        "\tattribute_id TEXT NOT NULL, \n"
        "\tuse_in_join_paths BOOLEAN DEFAULT true NOT NULL, \n"
        "\tmodel_version_id UUID NOT NULL, \n"
        "\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n"
        "\tupdated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n"
        "\tdeleted_at TIMESTAMP WITH TIME ZONE, \n"
        "\tCONSTRAINT pk_column__semantic_fk PRIMARY KEY (model_version_id, co"
        "lumn_id, attribute_id), \n"
        "\tCONSTRAINT fk_column__semantic_fk_column_id_catalog_column FOREIGN "
        "KEY(model_version_id, column_id) REFERENCES askdata.catalog_column ("
        "model_version_id, id), \n"
        "\tCONSTRAINT fk_column__semantic_fk_attribute_id_column_attribute FOR"
        "EIGN KEY(model_version_id, attribute_id) REFERENCES askdata.column_a"
        "ttribute (model_version_id, id), \n"
        "\tCONSTRAINT fk_column__semantic_fk_model_version_id_model_versions F"
        "OREIGN KEY(model_version_id) REFERENCES askdata.model_versions (id)\n"
        ")"
    ),
    (
        "CREATE TABLE askdata.column__union (\n"
        "\tsource_column_id TEXT NOT NULL, \n"
        "\ttarget_column_id TEXT NOT NULL, \n"
        "\trefs TEXT[] DEFAULT '{}' NOT NULL, \n"
        "\tmodel_version_id UUID NOT NULL, \n"
        "\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n"
        "\tupdated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n"
        "\tdeleted_at TIMESTAMP WITH TIME ZONE, \n"
        "\tCONSTRAINT pk_column__union PRIMARY KEY (model_version_id, source_c"
        "olumn_id, target_column_id), \n"
        "\tCONSTRAINT fk_column__union_target_column_id_catalog_column FOREIGN"
        " KEY(model_version_id, target_column_id) REFERENCES askdata.catalog_"
        "column (model_version_id, id), \n"
        "\tCONSTRAINT fk_column__union_source_column_id_catalog_column FOREIGN"
        " KEY(model_version_id, source_column_id) REFERENCES askdata.catalog_"
        "column (model_version_id, id), \n"
        "\tCONSTRAINT fk_column__union_model_version_id_model_versions FOREIGN"
        " KEY(model_version_id) REFERENCES askdata.model_versions (id)\n"
        ")"
    ),
    (
        "CREATE TABLE askdata.sql_query__column (\n"
        "\tsql_query_id TEXT NOT NULL, \n"
        "\tcolumn_id TEXT NOT NULL, \n"
        "\tmodel_version_id UUID NOT NULL, \n"
        "\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n"
        "\tupdated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n"
        "\tdeleted_at TIMESTAMP WITH TIME ZONE, \n"
        "\tCONSTRAINT pk_sql_query__column PRIMARY KEY (model_version_id, sql_"
        "query_id, column_id), \n"
        "\tCONSTRAINT fk_sql_query__column_sql_query_id_sql_query FOREIGN KEY("
        "model_version_id, sql_query_id) REFERENCES askdata.sql_query (model_"
        "version_id, id), \n"
        "\tCONSTRAINT fk_sql_query__column_column_id_catalog_column FOREIGN KE"
        "Y(model_version_id, column_id) REFERENCES askdata.catalog_column (mo"
        "del_version_id, id), \n"
        "\tCONSTRAINT fk_sql_query__column_model_version_id_model_versions FOR"
        "EIGN KEY(model_version_id) REFERENCES askdata.model_versions (id)\n"
        ")"
    ),
    (
        "CREATE TABLE askdata.tag_target (\n"
        "\tid TEXT DEFAULT (gen_random_uuid())::text NOT NULL, \n"
        "\ttag_id TEXT NOT NULL, \n"
        "\tterm_id TEXT, \n"
        "\ttable_id TEXT, \n"
        "\tcolumn_id TEXT, \n"
        "\tcolumn_attribute_id TEXT, \n"
        "\tsql_attribute_id TEXT, \n"
        "\ttagged TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n"
        "\ttagged_by TEXT, \n"
        "\trule_id TEXT, \n"
        "\tmodel_version_id UUID NOT NULL, \n"
        "\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n"
        "\tupdated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n"
        "\tdeleted_at TIMESTAMP WITH TIME ZONE, \n"
        "\tCONSTRAINT pk_tag_target PRIMARY KEY (model_version_id, id), \n"
        "\tCONSTRAINT ck_tag_target_exactly_one_target CHECK (num_nonnulls(ter"
        "m_id, table_id, column_id, column_attribute_id, sql_attribute_id) = "
        "1), \n"
        "\tCONSTRAINT fk_tag_target_rule_id_rule FOREIGN KEY(model_version_id,"
        " rule_id) REFERENCES askdata.rule (model_version_id, id), \n"
        "\tCONSTRAINT uq_tag_target_tag_id_table_id UNIQUE (model_version_id, "
        "tag_id, table_id), \n"
        "\tCONSTRAINT uq_tag_target_tag_id_column_attribute_id UNIQUE (model_v"
        "ersion_id, tag_id, column_attribute_id), \n"
        "\tCONSTRAINT fk_tag_target_tag_id_tag FOREIGN KEY(model_version_id, t"
        "ag_id) REFERENCES askdata.tag (model_version_id, id), \n"
        "\tCONSTRAINT fk_tag_target_column_id_catalog_column FOREIGN KEY(model"
        "_version_id, column_id) REFERENCES askdata.catalog_column (model_ver"
        "sion_id, id), \n"
        "\tCONSTRAINT uq_tag_target_tag_id_sql_attribute_id UNIQUE (model_vers"
        "ion_id, tag_id, sql_attribute_id), \n"
        "\tCONSTRAINT fk_tag_target_table_id_catalog_table FOREIGN KEY(model_v"
        "ersion_id, table_id) REFERENCES askdata.catalog_table (model_version"
        "_id, id), \n"
        "\tCONSTRAINT fk_tag_target_term_id_term FOREIGN KEY(model_version_id,"
        " term_id) REFERENCES askdata.term (model_version_id, id), \n"
        "\tCONSTRAINT fk_tag_target_sql_attribute_id_sql_attribute FOREIGN KEY"
        "(model_version_id, sql_attribute_id) REFERENCES askdata.sql_attribut"
        "e (model_version_id, id), \n"
        "\tCONSTRAINT uq_tag_target_tag_id_column_id UNIQUE (model_version_id,"
        " tag_id, column_id), \n"
        "\tCONSTRAINT uq_tag_target_tag_id_term_id UNIQUE (model_version_id, t"
        "ag_id, term_id), \n"
        "\tCONSTRAINT fk_tag_target_column_attribute_id_column_attribute FOREI"
        "GN KEY(model_version_id, column_attribute_id) REFERENCES askdata.col"
        "umn_attribute (model_version_id, id), \n"
        "\tCONSTRAINT fk_tag_target_model_version_id_model_versions FOREIGN KE"
        "Y(model_version_id) REFERENCES askdata.model_versions (id)\n"
        ")"
    ),
    ("CREATE INDEX ix_conversations_connection_id ON askdata.conversations (connection_id)"),
    ("CREATE INDEX ix_conversations_owner_id ON askdata.conversations (owner_id)"),
    ("CREATE INDEX ix_model_versions_connection_id ON askdata.model_versions (connection_id)"),
    ("CREATE INDEX ix_builds_connection_id ON askdata.builds (connection_id)"),
    (
        "CREATE INDEX ix_catalog_database_description_trgm ON askdata.catalog"
        "_database USING gin (description gin_trgm_ops)"
    ),
    ("CREATE INDEX ix_catalog_database_imported_id ON askdata.catalog_database (imported_id)"),
    (
        "CREATE INDEX ix_catalog_database_name_trgm ON askdata.catalog_databa"
        "se USING gin (name gin_trgm_ops)"
    ),
    (
        "CREATE INDEX ix_column_attribute_description_trgm ON askdata.column_"
        "attribute USING gin (description gin_trgm_ops)"
    ),
    ("CREATE INDEX ix_column_attribute_imported_id ON askdata.column_attribute (imported_id)"),
    (
        "CREATE INDEX ix_column_attribute_name_trgm ON askdata.column_attribu"
        "te USING gin (name gin_trgm_ops)"
    ),
    ("CREATE INDEX ix_column_attribute_table_id ON askdata.column_attribute (table_id)"),
    ("CREATE INDEX ix_column_attribute_term_name ON askdata.column_attribute (term_name)"),
    (
        "CREATE INDEX ix_custom_analysis_description_trgm ON askdata.custom_a"
        "nalysis USING gin (description gin_trgm_ops)"
    ),
    ("CREATE INDEX ix_custom_analysis_imported_id ON askdata.custom_analysis (imported_id)"),
    (
        "CREATE INDEX ix_custom_analysis_name_trgm ON askdata.custom_analysis"
        " USING gin (name gin_trgm_ops)"
    ),
    ("CREATE INDEX ix_rule_name_lower ON askdata.rule (lower(name))"),
    (
        "CREATE UNIQUE INDEX uq_rule_name_lower ON askdata.rule (model_versio"
        "n_id, lower(TRIM(BOTH FROM name)))"
    ),
    ("CREATE INDEX ix_runs_connection_id ON askdata.runs (connection_id)"),
    ("CREATE INDEX ix_runs_conversation_id ON askdata.runs (conversation_id)"),
    (
        "CREATE INDEX ix_sql_attribute_description_trgm ON askdata.sql_attrib"
        "ute USING gin (description gin_trgm_ops)"
    ),
    ("CREATE INDEX ix_sql_attribute_imported_id ON askdata.sql_attribute (imported_id)"),
    (
        "CREATE INDEX ix_sql_attribute_name_trgm ON askdata.sql_attribute USI"
        "NG gin (name gin_trgm_ops)"
    ),
    (
        "CREATE UNIQUE INDEX uq_sql_query_text_md5 ON askdata.sql_query (mode"
        "l_version_id, md5(sql_full_query))"
    ),
    ("CREATE INDEX ix_tag_name_trgm ON askdata.tag USING gin (name gin_trgm_ops)"),
    (
        "CREATE UNIQUE INDEX uq_tag_name_lower ON askdata.tag (model_version_"
        "id, lower(TRIM(BOTH FROM name)))"
    ),
    ("CREATE INDEX ix_term_description_trgm ON askdata.term USING gin (description gin_trgm_ops)"),
    ("CREATE INDEX ix_term_imported_id ON askdata.term (imported_id)"),
    ("CREATE INDEX ix_term_name ON askdata.term (name)"),
    ("CREATE INDEX ix_term_name_trgm ON askdata.term USING gin (name gin_trgm_ops)"),
    ("CREATE INDEX ix_zone_name_lower ON askdata.zone (lower(name))"),
    ("CREATE INDEX ix_catalog_schema_database_id ON askdata.catalog_schema (database_id)"),
    (
        "CREATE INDEX ix_catalog_schema_description_trgm ON askdata.catalog_s"
        "chema USING gin (description gin_trgm_ops)"
    ),
    ("CREATE INDEX ix_catalog_schema_imported_id ON askdata.catalog_schema (imported_id)"),
    (
        "CREATE INDEX ix_catalog_schema_name_trgm ON askdata.catalog_schema U"
        "SING gin (name gin_trgm_ops)"
    ),
    ("CREATE INDEX ix_column_attribute__term_term_id ON askdata.column_attribute__term (term_id)"),
    ("CREATE INDEX ix_events_run_id ON askdata.events (run_id)"),
    ("CREATE INDEX ix_rule__tag_tag_id ON askdata.rule__tag (tag_id)"),
    ("CREATE INDEX ix_sql_attribute__term_term_id ON askdata.sql_attribute__term (term_id)"),
    (
        "CREATE INDEX ix_catalog_table_description_trgm ON askdata.catalog_ta"
        "ble USING gin (description gin_trgm_ops)"
    ),
    ("CREATE INDEX ix_catalog_table_imported_id ON askdata.catalog_table (imported_id)"),
    ("CREATE INDEX ix_catalog_table_name ON askdata.catalog_table (name)"),
    (
        "CREATE INDEX ix_catalog_table_name_trgm ON askdata.catalog_table USI"
        "NG gin (name gin_trgm_ops)"
    ),
    ("CREATE INDEX ix_catalog_table_schema_id ON askdata.catalog_table (schema_id)"),
    (
        "CREATE INDEX ix_catalog_column_description_trgm ON askdata.catalog_c"
        "olumn USING gin (description gin_trgm_ops)"
    ),
    ("CREATE INDEX ix_catalog_column_imported_id ON askdata.catalog_column (imported_id)"),
    ("CREATE INDEX ix_catalog_column_name ON askdata.catalog_column (name)"),
    (
        "CREATE INDEX ix_catalog_column_name_trgm ON askdata.catalog_column U"
        "SING gin (name gin_trgm_ops)"
    ),
    ("CREATE INDEX ix_catalog_column_table_id ON askdata.catalog_column (table_id)"),
    ("CREATE INDEX ix_sql_query__table_table_id ON askdata.sql_query__table (table_id)"),
    ("CREATE INDEX ix_table__join_target ON askdata.table__join (target_table_id)"),
    ("CREATE INDEX ix_table__term_term_id ON askdata.table__term (term_id)"),
    ("CREATE INDEX ix_zone_target_database_id ON askdata.zone_target (database_id)"),
    ("CREATE INDEX ix_zone_target_schema_id ON askdata.zone_target (schema_id)"),
    ("CREATE INDEX ix_zone_target_table_id ON askdata.zone_target (table_id)"),
    ("CREATE INDEX ix_zone_target_zone_id ON askdata.zone_target (zone_id)"),
    (
        "CREATE INDEX ix_column__foreign_key_target ON askdata.column__foreig"
        "n_key (target_column_id)"
    ),
    (
        "CREATE INDEX ix_column__has_attribute_attribute_id ON askdata.column"
        "__has_attribute (attribute_id)"
    ),
    (
        "CREATE INDEX ix_column__semantic_fk_attribute_id ON askdata.column__"
        "semantic_fk (attribute_id)"
    ),
    ("CREATE INDEX ix_sql_query__column_column_id ON askdata.sql_query__column (column_id)"),
    ("CREATE INDEX ix_tag_target_column_attribute_id ON askdata.tag_target (column_attribute_id)"),
    ("CREATE INDEX ix_tag_target_column_id ON askdata.tag_target (column_id)"),
    ("CREATE INDEX ix_tag_target_rule_id ON askdata.tag_target (rule_id)"),
    ("CREATE INDEX ix_tag_target_sql_attribute_id ON askdata.tag_target (sql_attribute_id)"),
    ("CREATE INDEX ix_tag_target_table_id ON askdata.tag_target (table_id)"),
    ("CREATE INDEX ix_tag_target_term_id ON askdata.tag_target (term_id)"),
]
TABLES = [
    "configurations",
    "conversations",
    "model_versions",
    "analysis",
    "builds",
    "catalog_database",
    "column_attribute",
    "custom_analysis",
    "rule",
    "runs",
    "semantic_compilation_history",
    "sql_attribute",
    "sql_query",
    "tag",
    "term",
    "text_attribute",
    "zone",
    "catalog_schema",
    "column_attribute__term",
    "custom_analysis__sql",
    "events",
    "rule__tag",
    "sql_attribute__sql",
    "sql_attribute__term",
    "catalog_table",
    "catalog_column",
    "sql_query__table",
    "table__join",
    "table__term",
    "zone_target",
    "column__foreign_key",
    "column__has_attribute",
    "column__join",
    "column__semantic_fk",
    "column__union",
    "sql_query__column",
    "tag_target",
]


def upgrade():
    op.execute("CREATE SCHEMA askdata")
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm WITH SCHEMA public")
    for statement in DDL:
        op.execute(sa.text(statement))
    op.execute("""CREATE FUNCTION askdata.touch_updated_at() RETURNS trigger
        LANGUAGE plpgsql AS $$ BEGIN NEW.updated_at = now(); RETURN NEW; END $$""")
    for name in TABLES:
        op.execute(
            sa.text(
                f'CREATE TRIGGER touch_updated_at BEFORE UPDATE ON askdata."{name}" '
                "FOR EACH ROW EXECUTE FUNCTION askdata.touch_updated_at()"
            )
        )
    op.execute("""INSERT INTO role_permissions(id,role_id,permission,created_at,updated_at)
        SELECT gen_random_uuid(),r.id,p.permission,now(),now()
        FROM roles r CROSS JOIN
          (VALUES ('askdata:read'),('askdata:run'),('askdata:manage')) p(permission)
        WHERE r.deleted_at IS NULL AND (p.permission='askdata:read' OR
          (p.permission='askdata:run' AND r.code IN ('operator','admin')) OR
          (p.permission='askdata:manage' AND r.code='admin'))
          AND r.code IN ('viewer','operator','admin')
        ON CONFLICT (role_id,permission)
        DO UPDATE SET deleted_at=NULL,updated_at=now()""")
    op.execute(
        "UPDATE roles SET permissions_version=permissions_version+1,updated_at=now() "
        "WHERE code IN ('viewer','operator','admin')"
    )


def downgrade():
    bind = op.get_bind()
    for name in TABLES:
        if bind.scalar(sa.text(f'SELECT EXISTS(SELECT 1 FROM askdata."{name}")')):
            raise RuntimeError("Ask-data records exist; preserve them before rollback")
    if bind.scalar(
        sa.text("SELECT EXISTS(SELECT 1 FROM access_grants WHERE permission LIKE 'askdata:%')")
    ):
        raise RuntimeError("Ask-data grants exist; preserve them before rollback")
    for name in reversed(TABLES):
        op.execute(sa.text(f'DROP TABLE askdata."{name}"'))
    op.execute("DROP FUNCTION askdata.touch_updated_at()")
    op.execute("DROP SCHEMA askdata")
    op.execute(
        "UPDATE role_permissions SET deleted_at=now(),updated_at=now() WHERE permission IN "
        "('askdata:read','askdata:run','askdata:manage')"
    )
    op.execute(
        "UPDATE roles SET permissions_version=permissions_version+1,updated_at=now() "
        "WHERE code IN ('viewer','operator','admin')"
    )
