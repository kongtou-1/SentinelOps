"""Model version lifecycle: draft, build, publish and retire."""

from datetime import UTC, datetime

from fastapi import HTTPException
from sqlalchemy import select

from app.models import AskDataBuild, AskDataConfiguration, AskDataModelVersion, SourceSnapshot, User
from app.repositories import askdata as repository
from app.schemas.askdata import (
    AskDataBuildResponse,
    AskDataVersionResponse,
)
from app.services import access_control, service_databases
from app.services import askdata as settings


def _names(db, identifiers):
    if not identifiers:
        return {}
    rows = db.execute(select(User.id, User.username).where(User.id.in_(identifiers))).all()
    return {identifier: username for identifier, username in rows}


def _check_stale(connection, version):
    if (version.config_version, version.schema_hash) != (
        connection.config_version,
        connection.schema_hash,
    ):
        raise HTTPException(409, "数据库配置或结构已变化，请重新构建")
    if not connection.enabled or not connection.query_enabled:
        raise HTTPException(409, "数据库连接已停用或未开启只读查询")
    if connection.permission_status != "verified":
        raise HTTPException(409, "只读权限尚未核验")


def _snapshot(db, user, connection, identifier):
    if identifier is None:
        return
    snapshot = db.get(SourceSnapshot, identifier)
    if (
        snapshot is None
        or snapshot.deleted_at
        or snapshot.application_id != connection.application_id
    ):
        raise HTTPException(404, "模型源码不可用")
    access_control.require(db, user, "sources:read", "application", snapshot.application_id)


def _available(db, connection):
    if not connection.query_enabled or connection.permission_status != "verified":
        raise HTTPException(409, "语义构建需要已启用且权限已核验的只读连接")
    configuration = repository.configuration(db, connection.id)
    if configuration and (configuration.deleted_at or not configuration.enabled):
        raise HTTPException(409, "该连接的问数功能已停用")
    try:
        settings.check_build_capabilities()
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(503, "语义构建能力不可用") from exc


def serialize(db, version, *, configuration=None, names=None):
    build = repository.latest_builds(db, [version.id]).get(version.id)
    return AskDataVersionResponse(
        id=version.id,
        connection_id=version.connection_id,
        source_snapshot_id=version.source_snapshot_id,
        created_by=version.created_by,
        created_by_name=(names or _names(db, [version.created_by])).get(version.created_by, ""),
        config_version=version.config_version,
        schema_hash=version.schema_hash,
        status=version.status,
        context=version.context or {},
        published_at=version.published_at,
        active=bool(
            configuration
            and configuration.active_model_version_id == version.id
            and not configuration.deleted_at
        ),
        build=AskDataBuildResponse.model_validate(build) if build else None,
        created_at=version.created_at,
        updated_at=version.updated_at,
    )


def listing(db, user, connection_id):
    settings.authorized_connection(db, user, connection_id)
    versions = repository.versions(db, connection_id)
    configuration = repository.configuration(db, connection_id)
    names = _names(db, {version.created_by for version in versions})
    return [
        serialize(db, version, configuration=configuration, names=names) for version in versions
    ]


def detail(db, user, identifier):
    version = repository.model_version(db, identifier)
    if version is None:
        raise HTTPException(404, "问数模型版本不存在")
    settings.authorized_connection(db, user, version.connection_id)
    return serialize(db, version, configuration=repository.configuration(db, version.connection_id))


def create(db, user, connection_id, body):
    connection = settings.authorized_connection(db, user, connection_id, "askdata:manage")
    settings.require_active()
    db.refresh(connection, with_for_update=True)
    _available(db, connection)
    if not connection.schema_hash:
        raise HTTPException(409, "请先刷新数据库结构")
    if repository.active_build(db, connection_id) is not None:
        raise HTTPException(409, "该连接已有构建任务")
    # Prefer the snapshot bound to the release deployed in the target
    # environment; an explicit choice is always labelled as a manual reference.
    from app.services import askdata_code_rules as code_rules

    snapshot_id, origin, label = code_rules.apply_snapshot_context(db, user, connection, body)
    from app.services import askdata_settings

    vector = askdata_settings.execution_context(db, connection_id)
    version = AskDataModelVersion(
        connection_id=connection_id,
        source_snapshot_id=snapshot_id,
        created_by=user.id,
        config_version=connection.config_version,
        schema_hash=connection.schema_hash,
        vector_signature=vector["vector_signature"],
        status="draft",
        context={"source_origin": origin, "source_label": label},
    )
    repository.save(db, version)
    db.flush()
    build = AskDataBuild(
        connection_id=connection_id,
        model_version_id=version.id,
        requested_by=user.id,
        status="queued",
        step="queued",
        detail={},
    )
    repository.save(db, build)
    service_databases.repository.audit(db, connection, user, "askdata_model_build_requested")
    db.commit()
    db.refresh(version)
    return serialize(db, version)


def publish(db, user, identifier):
    version = repository.model_version(db, identifier)
    if version is None:
        raise HTTPException(404, "问数模型版本不存在")
    connection = settings.authorized_connection(db, user, version.connection_id, "askdata:manage")
    if version.status != "ready":
        raise HTTPException(409, "仅构建成功的模型可以启用")
    _check_stale(connection, version)
    configuration = repository.configuration(db, version.connection_id, lock=True)
    if configuration is None:
        configuration = AskDataConfiguration(connection_id=version.connection_id)
        repository.save(db, configuration)
        db.flush()
    if configuration.deleted_at or not configuration.enabled:
        raise HTTPException(409, "该连接的问数功能已停用")
    previous = (
        repository.model_version(db, configuration.active_model_version_id)
        if configuration.active_model_version_id
        else None
    )
    if previous is not None and previous.id != version.id and previous.status == "ready":
        previous.status = "retired"
    configuration.active_model_version_id = version.id
    version.published_at = datetime.now(UTC)
    service_databases.repository.audit(db, connection, user, "askdata_model_published")
    db.commit()
    db.refresh(version)
    return serialize(db, version, configuration=configuration)


def retire(db, user, identifier):
    version = repository.model_version(db, identifier)
    if version is None:
        raise HTTPException(404, "问数模型版本不存在")
    connection = settings.authorized_connection(db, user, version.connection_id, "askdata:manage")
    configuration = repository.configuration(db, version.connection_id, lock=True)
    if configuration is None or configuration.active_model_version_id != version.id:
        raise HTTPException(409, "仅当前启用的模型可以停用")
    configuration.active_model_version_id = None
    version.status = "retired"
    service_databases.repository.audit(db, connection, user, "askdata_model_retired")
    db.commit()
    db.refresh(version)
    return serialize(db, version, configuration=configuration)


def cancel(db, user, identifier):
    version = repository.model_version(db, identifier)
    if version is None:
        raise HTTPException(404, "问数模型版本不存在")
    connection = settings.authorized_connection(db, user, version.connection_id, "askdata:manage")
    build = repository.active_build(db, version.connection_id)
    if build is None or build.model_version_id != version.id:
        raise HTTPException(409, "该版本没有进行中的构建")
    if build.status == "queued":
        build.status, build.finished_at = "cancelled", datetime.now(UTC)
        version.status = "failed"
        version.context = {**(version.context or {}), "error_code": "build_cancelled"}
    build.cancel_requested = True
    service_databases.repository.audit(db, connection, user, "askdata_model_build_cancelled")
    db.commit()
    db.refresh(version)
    return serialize(db, version)


def configuration(db, user, connection_id):
    settings.authorized_connection(db, user, connection_id)
    row = repository.configuration(db, connection_id)
    return {
        "connection_id": connection_id,
        "enabled": bool(row and row.enabled and not row.deleted_at),
        "version": row.version if row else 0,
        "active_model_version_id": row.active_model_version_id if row else None,
    }
