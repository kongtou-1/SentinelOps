import json
import re
from datetime import UTC, datetime

from fastapi import HTTPException

from app.integrations import database_readonly as adapter
from app.integrations.database_sql import checked_query
from app.repositories import database_diagnosis as repository
from app.schemas.diagnostic_tools import Empty
from app.schemas.service_databases import DatabaseQueryInput, DatabaseSchemaInput
from app.services import service_databases as service
from app.services.incidents.logic import linked_alerts
from app.utils.docker import docker_identifier
from app.utils.redaction import redact

DATABASE_TOOLS = {
    "list_service_databases": Empty,
    "get_database_schema": DatabaseSchemaInput,
    "query_database_readonly": DatabaseQueryInput,
}


def freeze_database_context(db, incident_id):
    result = {"version": 1, "connections": [], "data_gaps": []}
    seen = set()
    for alert in linked_alerts(db, incident_id):
        app_id, environment = None, None
        monitor = (
            repository.monitor(db, alert.service_monitor_id) if alert.service_monitor_id else None
        )
        task = (
            repository.prediction(db, alert.prediction_task_id)
            if alert.prediction_task_id
            else None
        )
        candidate = (
            (monitor.container_id if monitor and not monitor.deleted_at else None)
            or (task.container_id if task and not task.deleted_at else None)
            or alert.labels.get("container_id")
        )
        container = repository.target(
            db,
            candidate,
            alert.server_id,
            docker_identifier(alert.labels.get("docker_id") or alert.labels.get("id") or ""),
        )
        if monitor and not monitor.deleted_at:
            app_id = monitor.application_id
        elif container:
            app_id = container.application_id
        elif alert.llm_endpoint_id:
            apps = repository.applications_for_endpoint(db, alert.llm_endpoint_id)
            if len(apps) == 1:
                app_id = apps[0].id
            elif len(apps) > 1:
                result["data_gaps"].append("database_service_ambiguous")
        if not app_id:
            continue  # Host-level scope never expands to all colocated containers.
        if container:
            deployments = repository.deployments(db, container.id, alert.starts_at)
            if len(deployments) > 1:
                result["data_gaps"].append("database_environment_ambiguous")
                continue
            if deployments:
                if monitor and deployments[0].application_id != app_id:
                    result["data_gaps"].append("database_service_ambiguous")
                    continue
                app_id, environment = deployments[0].application_id, deployments[0].environment
            else:
                if monitor and container.application_id != app_id:
                    result["data_gaps"].append("database_service_ambiguous")
                    continue
                server = repository.server(db, container.server_id)
                environment = server.environment if server else None
        if environment is None:
            environments = repository.environments(db, app_id)
            if len(environments) == 1:
                environment = next(iter(environments))
        if environment is None:
            result["data_gaps"].append("database_environment_ambiguous")
            continue
        for row in repository.connections(db, app_id, environment):
            if row.id not in seen:
                entry = service.frozen(row)
                metadata_scope = row.allowed_tables or {
                    key: [c["name"] for c in table["columns"]]
                    for key, table in row.schema_cache.get("tables", {}).items()
                }
                # Freeze authorized schema only, without copying the full database catalog.
                entry["schema"] = {
                    key: {
                        **row.schema_cache.get("tables", {}).get(key, {}),
                        "columns": [
                            c
                            for c in row.schema_cache.get("tables", {})
                            .get(key, {})
                            .get("columns", [])
                            if c["name"] in columns
                        ],
                    }
                    for key, columns in metadata_scope.items()
                }
                result["connections"].append(entry)
                seen.add(row.id)
    result["data_gaps"] = sorted(set(result["data_gaps"]))
    if len(json.dumps(result, ensure_ascii=False).encode()) > 65536:
        return {"version": 1, "connections": [], "data_gaps": ["database_context_too_large"]}
    return redact(result)


def current_entry(db, context, identifier):
    entry = next(
        (c for c in context.get("connections", []) if c["connection_id"] == str(identifier)), None
    )
    if not entry:
        raise PermissionError("database_outside_incident")
    row = service.active(db, identifier)
    app = service.repository.application(db, row.application_id)
    if (
        not row.enabled
        or not app
        or not app.enabled
        or service.frozen(row) != {key: value for key, value in entry.items() if key != "schema"}
    ):
        raise ValueError("database_configuration_changed")
    return row, entry


def database_evidence_valid(db, context, item, *, run_started_at=None):
    if item.get("source") == "get_alert_context":
        saved = item.get("data", {}).get("database_context", {})
        return saved == context
    if item.get("source") not in DATABASE_TOOLS:
        return True
    data = item.get("data", {})
    if data.get("context_hash") != service.digest(context):
        return False
    if run_started_at and data.get("run_started_at") != run_started_at.isoformat():
        return False
    if not data.get("connection_id"):
        return all(
            _entry_valid(db, context, entry["connection_id"])
            for entry in context.get("connections", [])
        )
    return _entry_valid(db, context, data["connection_id"])


def _entry_valid(db, context, identifier):
    from uuid import UUID

    try:
        current_entry(db, context, UUID(identifier))
        return True
    except (HTTPException, PermissionError, ValueError):
        return False


def dispatch_database(db, run, name, body):
    context = run.database_context
    base = {
        "context_hash": service.digest(context),
        "run_started_at": run.started_at.isoformat() if run.started_at else None,
    }
    if name == "list_service_databases":
        available = [
            c
            for c in context.get("connections", [])
            if _entry_valid(db, context, c["connection_id"])
        ]
        return {
            **base,
            "connections": [
                {
                    **{k: v for k, v in c.items() if k != "schema"},
                    "schema_tables": list(c["schema"]),
                }
                for c in available
            ],
            "data_gaps": context.get("data_gaps", []),
        }
    base["connection_id"] = str(body.connection_id)
    try:
        row, entry = current_entry(db, context, body.connection_id)
        base.update(
            application_id=str(row.application_id),
            config_version=row.config_version,
            schema_hash=row.schema_hash,
        )
        if name == "get_database_schema":
            tables = body.tables or list(entry["schema"])[:10]
            if any(table not in entry["schema"] for table in tables):
                raise ValueError("table_not_allowed")
            return {
                **base,
                "tables": {table: entry["schema"][table] for table in tables},
                "schema_refreshed_at": entry["schema_refreshed_at"],
                "truncated": entry["schema_truncated"],
                "description": entry["description"],
                "table_notes": {t: entry["table_notes"].get(t, "") for t in tables},
            }
        if not row.query_enabled:
            raise ValueError("database_queries_disabled")
        sql, summary, sql_hash = checked_query(
            body.sql, row.engine, row.allowed_tables, entry["schema"]
        )
        with service.connection_lock(row.id), adapter.connect(row) as connection:
            if adapter.permissions(row, connection) != "verified":
                raise ValueError("database_permissions_unverified")
            value = adapter.query(connection, sql)
        # Recheck revocation/cancellation after an external read, before returning any records.
        db.expire_all()
        current_entry(db, context, body.connection_id)
        db.refresh(run)
        if run.status != "running":
            raise ValueError("diagnosis_stopped")
        value = redact(value)
        return {
            **base,
            **value,
            "sql": summary,
            "query_hash": sql_hash,
            "content_hash": service.digest(value),
        }
    except (adapter.DatabaseUnavailable, ValueError) as exc:
        # Messages from our own validators only, never raw driver exception text.
        code = str(exc) if re.fullmatch(r"[a-z_]{1,80}", str(exc)) else "invalid_query"
        return {
            **base,
            "availability": "unavailable",
            "error_code": code,
            "observed_at": datetime.now(UTC).isoformat(),
        }
    except HTTPException as exc:
        return {
            **base,
            "availability": "unavailable",
            "error_code": ("database_busy" if exc.status_code == 409 else "database_unavailable"),
        }
