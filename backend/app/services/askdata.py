"""Ask-data configuration and authorization; orchestration stays in services."""

import os
from contextlib import contextmanager
from dataclasses import dataclass, replace

from fastapi import HTTPException

from app.db.session import SessionLocal
from app.repositories import askdata as repository
from app.repositories.ontology import OntologyStore
from app.schemas.askdata import (
    AskDataConfigurationResponse,
    AskDataConnectionResponse,
    AskDataContextResponse,
)
from app.services import access_control, service_databases

_PAUSED_VALUES = {"1", "true", "yes", "on"}


def paused() -> bool:
    """Global kill switch for new ask-data work.

    Operators set ``ASKDATA_PAUSED=true`` and restart the backend to stop
    accepting new builds and questions. Existing rows, results and read APIs
    keep working, so the switch is reversible without touching data.
    """
    return os.environ.get("ASKDATA_PAUSED", "").strip().lower() in _PAUSED_VALUES


def require_active() -> None:
    if paused():
        raise HTTPException(409, "智能问数已暂停，新增构建与问数任务暂不受理")


def authorized_connection(db, user, identifier, permission="askdata:read"):
    access_control.require(db, user, permission, "service_database", identifier)
    access_control.require(db, user, "service_databases:read", "service_database", identifier)
    row = repository.connection(db, identifier)
    if row is None:
        raise HTTPException(404, "数据库连接不存在")
    # Check lifecycle even when the caller is an administrator.
    return service_databases.active(db, identifier)


def connections(db, user, application_id=None):
    rows = service_databases.repository.listing(db, application_id)
    authorization = access_control.context(db, user)
    rows = [row for row in rows if authorization.allows("askdata:read", "service_database", row.id)]
    settings = {r.connection_id: r for r in repository.configurations(db, [row.id for row in rows])}
    return [
        AskDataConnectionResponse(
            **service_databases.response(row).model_dump(),
            askdata_enabled=(settings[row.id].enabled and not settings[row.id].deleted_at)
            if row.id in settings
            else True,
            askdata_version=settings[row.id].version if row.id in settings else 0,
        )
        for row in rows
    ]


def connection_context(db, user, identifier):
    row = authorized_connection(db, user, identifier)
    configuration = repository.configuration(db, identifier)
    return AskDataContextResponse(
        connection_id=row.id,
        application_id=row.application_id,
        environment=row.environment,
        config_version=row.config_version,
        schema_hash=row.schema_hash,
        schema_refreshed=row.schema_refreshed_at is not None,
        query_enabled=row.query_enabled,
        permission_status=row.permission_status,
        enabled=(configuration.enabled and not configuration.deleted_at) if configuration else True,
        version=configuration.version if configuration else 0,
        active_model_version_id=(
            configuration.active_model_version_id
            if configuration and not configuration.deleted_at
            else None
        ),
    )


def check_build_capabilities():
    """Fail before queueing work that cannot complete in this deployment."""
    from app.core.config import get_settings
    from app.services.agent_config import profile

    if not get_settings().ai_enabled:
        raise HTTPException(503, "智能问数需要启用 AI")
    config = profile("diagnosis")
    if not config["enabled"] or not config["channels"]:
        raise HTTPException(503, "智能问数需要可用的模型通道")
    from app.services.knowledge.index import encoder

    encoder()


def configure(db, user, identifier, body):
    from app.models import AskDataConfiguration

    connection = authorized_connection(db, user, identifier, "askdata:manage")
    # Serialize creation as well as edits for the same connection.
    db.refresh(connection, with_for_update=True)
    row = repository.configuration(db, identifier, lock=True)
    if body.expected_version != (row.version if row else 0):
        raise HTTPException(409, "问数配置已变化，请刷新后重试")
    if row is None:
        row = AskDataConfiguration(connection_id=identifier)
        repository.save(db, row)
    row.enabled, row.deleted_at = body.enabled, None
    row.version = (row.version or 0) + 1
    service_databases.repository.audit(db, connection, user, "askdata_configured")
    db.commit()
    db.refresh(row)
    return AskDataConfigurationResponse.model_validate(row)


def validate_version(db, user, connection_id, version_id, permission):
    connection = authorized_connection(db, user, connection_id, permission)
    version = repository.model_version(db, version_id)
    configuration = repository.configuration(db, connection_id)
    if configuration and (configuration.deleted_at or not configuration.enabled):
        raise HTTPException(409, "该连接的问数功能已停用")
    if not connection.enabled or version is None or version.connection_id != connection_id:
        raise HTTPException(404, "问数模型版本不存在或连接已停用")
    if version.status in {"failed", "retired"}:
        raise HTTPException(409, "问数模型版本不可用")
    if (version.config_version, version.schema_hash) != (
        connection.config_version,
        connection.schema_hash,
    ):
        raise HTTPException(409, "数据库配置或结构已变化")
    from app.services import askdata_settings

    signature = askdata_settings.execution_context(db, connection_id)["vector_signature"]
    if version.vector_signature and version.vector_signature != signature:
        # Vectors from a different embedding model cannot answer this query.
        raise HTTPException(409, "向量模型已变更，请重新构建业务模型")
    if version.source_snapshot_id:
        from app.models import SourceSnapshot

        snapshot = db.get(SourceSnapshot, version.source_snapshot_id)
        if (
            snapshot is None
            or snapshot.deleted_at
            or snapshot.application_id != connection.application_id
        ):
            raise HTTPException(404, "模型源码不可用")
        access_control.require(db, user, "sources:read", "application", snapshot.application_id)
    return version


@dataclass(frozen=True)
class StorageAccess:
    store_factory: object
    transaction_factory: object


@contextmanager
def storage(db, user, connection_id, version_id, permission="askdata:read"):
    """Bind the existing Session. The caller must commit or roll back its unit of work."""
    validate_version(db, user, connection_id, version_id, permission)

    def guard(writing):
        # Independent authorization read observes revocation during a long-running task.
        with SessionLocal() as check:
            actor = repository.active_user(check, user.id)
            if actor is None:
                raise HTTPException(403, "问数权限已失效")
            validate_version(
                check, actor, connection_id, version_id, "askdata:manage" if writing else permission
            )

    store = OntologyStore(db, version_id, guard)

    @contextmanager
    def transaction():
        guard(True)
        with db.begin_nested():
            yield

    yield StorageAccess(lambda: store, transaction)


@contextmanager
def bind_storage(db, user, bindings, permission="askdata:read"):
    """Attach real storage to the task's model/vector/connector factories."""
    from app.integrations.ontology import task

    if bindings.user_id != user.id:
        raise HTTPException(403, "问数任务用户不匹配")
    with storage(db, user, bindings.connection_id, bindings.model_version_id, permission) as access:
        with task(
            replace(
                bindings,
                store_factory=access.store_factory,
                transaction_factory=access.transaction_factory,
            )
        ):
            yield


def database_connector(user_id, connection_id, version_id, *, hold_lock=False, gate="diagnosis"):
    from contextlib import nullcontext
    from copy import deepcopy

    from app.integrations import database_readonly as adapter
    from app.integrations.askdata_database import PlatformDatabase
    from app.integrations.askdata_sql import checked_askdata_query
    from app.integrations.database_sql import checked_query

    # Semantic builds keep the original small dialect. Ask-data runs own a
    # separate gate that understands CTEs and window functions.
    validator = checked_askdata_query if gate == "askdata" else checked_query

    def actor_and_connection(db, permission):
        actor = repository.active_user(db, user_id)
        if actor is None:
            raise HTTPException(403, "问数权限已失效")
        validate_version(db, actor, connection_id, version_id, permission)
        return authorized_connection(db, actor, connection_id, permission)

    def load():
        with SessionLocal() as db:
            row = actor_and_connection(db, "askdata:read")
            if row.schema_cache.get("truncated"):
                raise ValueError("database_schema_incomplete")
            tables = deepcopy(row.schema_cache.get("tables", {}))
            for key, note in (row.table_notes or {}).items():
                if key in tables and note:
                    tables[key]["comment"] = note
            if row.query_enabled:
                tables = {
                    key: {
                        **table,
                        "columns": [
                            c for c in table["columns"] if c["name"] in row.allowed_tables[key]
                        ],
                    }
                    for key, table in tables.items()
                    if key in row.allowed_tables
                }
                for key, table in tables.items():
                    allowed = set(row.allowed_tables[key])
                    if not set(table.get("primary_key", [])) <= allowed:
                        table["primary_key"] = []
                    table["foreign_keys"] = [
                        fk
                        for fk in table.get("foreign_keys", [])
                        if set(fk["columns"]) <= allowed
                        and fk["referenced_table"] in tables
                        and set(fk["referenced_columns"])
                        <= set(row.allowed_tables[fk["referenced_table"]])
                    ]
                    # Index definitions may mention columns outside the authorized scope.
                    table.pop("indexes", None)
            from app.services import askdata_settings

            return {
                "engine": row.engine,
                "tables": tables,
                "query_enabled": row.query_enabled,
                "sensitive_specs": askdata_settings.masking_specs(db, connection_id),
                "sensitive_exclusions": askdata_settings.sampling_exclusions(
                    db, connection_id, tables
                ),
            }

    def execute(sql):
        with SessionLocal() as db:
            row = actor_and_connection(db, "askdata:run")
            if not row.query_enabled:
                raise ValueError("database_queries_disabled")
            statement, _, shape = validator(
                sql, row.engine, row.allowed_tables, row.schema_cache.get("tables", {})
            )
            lock = nullcontext() if hold_lock else service_databases.connection_lock(row.id)
            with lock, adapter.connect(row) as remote:
                if adapter.permissions(row, remote) != "verified":
                    raise ValueError("database_permissions_unverified")
                result = adapter.query(remote, statement, include_types=gate == "askdata")
        with SessionLocal() as check:
            actor_and_connection(check, "askdata:run")
        result["executed_sql"] = statement
        result["sql_shape"] = shape
        return result

    def source_queries(snapshot):
        # Static, grant-checked SQL extracted from the pinned source snapshot.
        from app.services import askdata_code_rules as code_rules

        dialect = "postgres" if snapshot["engine"] == "postgresql" else "mysql"
        return code_rules.source_queries(
            connection_id, version_id, snapshot["tables"], dialect
        )

    load()
    return PlatformDatabase(connection_id, load, execute, query_provider=source_queries)
