"""Storage operations on a caller-owned platform Session."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import select, text, update

from app.models import (
    AskDataBuild,
    AskDataConfiguration,
    AskDataConversation,
    AskDataEmbedding,
    AskDataModelVersion,
    AskDataRun,
    ServiceDatabase,
    User,
)


def versions(db, connection_id):
    return db.scalars(
        select(AskDataModelVersion)
        .where(
            AskDataModelVersion.connection_id == connection_id,
            AskDataModelVersion.deleted_at.is_(None),
        )
        .order_by(AskDataModelVersion.created_at.desc(), AskDataModelVersion.id)
        .execution_options(access_internal=True, populate_existing=True)
    ).all()


def latest_builds(db, version_ids):
    if not version_ids:
        return {}
    rows = db.scalars(
        select(AskDataBuild)
        .where(AskDataBuild.model_version_id.in_(version_ids), AskDataBuild.deleted_at.is_(None))
        .order_by(AskDataBuild.created_at.desc(), AskDataBuild.id)
        .execution_options(access_internal=True, populate_existing=True)
    ).all()
    latest = {}
    for row in rows:
        latest.setdefault(row.model_version_id, row)
    return latest


def build(db, identifier, *, lock=False):
    statement = (
        select(AskDataBuild)
        .where(AskDataBuild.id == identifier, AskDataBuild.deleted_at.is_(None))
        .execution_options(access_internal=True, populate_existing=True)
    )
    if lock:
        statement = statement.with_for_update()
    return db.scalar(statement)


def active_build(db, connection_id):
    return db.scalar(
        select(AskDataBuild)
        .where(
            AskDataBuild.connection_id == connection_id,
            AskDataBuild.status.in_(("queued", "running")),
            AskDataBuild.deleted_at.is_(None),
        )
        .execution_options(access_internal=True, populate_existing=True)
    )


def claim_build(db):
    row = db.scalar(
        select(AskDataBuild)
        .where(AskDataBuild.status == "queued", AskDataBuild.deleted_at.is_(None))
        .order_by(AskDataBuild.created_at, AskDataBuild.id)
        .with_for_update(skip_locked=True)
        .execution_options(access_internal=True, populate_existing=True)
    )
    if row is not None:
        row.status, row.started_at = "running", datetime.now(UTC)
    return row


def recover_interrupted(db, stale_before):
    rows = db.scalars(
        select(AskDataBuild)
        .where(
            AskDataBuild.status == "running",
            AskDataBuild.deleted_at.is_(None),
            AskDataBuild.started_at < stale_before,
        )
        .execution_options(access_internal=True, populate_existing=True)
    ).all()
    now = datetime.now(UTC)
    for row in rows:
        row.status, row.error_code, row.finished_at = "interrupted", "build_interrupted", now
        version = db.get(AskDataModelVersion, row.model_version_id)
        if version is not None:
            version.status = "failed"
            version.context = {**(version.context or {}), "error_code": "build_interrupted"}
    return rows


def running_builds(db):
    return db.scalars(
        select(AskDataBuild).where(
            AskDataBuild.status == "running", AskDataBuild.deleted_at.is_(None)
        )
    ).all()


def queue_build(db, build):
    db.add(build)


def versions_for_connection(db, connection_id, exclude=None):
    statement = select(AskDataModelVersion).where(
        AskDataModelVersion.connection_id == connection_id,
        AskDataModelVersion.deleted_at.is_(None),
    )
    if exclude is not None:
        statement = statement.where(AskDataModelVersion.id != exclude)
    return db.scalars(statement).all()


def retire_versions(db, connection_id, keep_id=None):
    now = datetime.now(UTC)
    statement = (
        update(AskDataModelVersion)
        .where(
            AskDataModelVersion.connection_id == connection_id,
            AskDataModelVersion.status == "ready",
            AskDataModelVersion.deleted_at.is_(None),
        )
        .values(status="retired", updated_at=now)
    )
    if keep_id is not None:
        statement = statement.where(AskDataModelVersion.id != keep_id)
    db.execute(statement.execution_options(access_internal=True))


def embeddings(db, version_id, collection=None):
    statement = select(AskDataEmbedding).where(
        AskDataEmbedding.model_version_id == version_id,
        AskDataEmbedding.deleted_at.is_(None),
    )
    if collection is not None:
        statement = statement.where(AskDataEmbedding.collection == collection)
    return db.scalars(statement).all()


def stale_build_cutoff(seconds=30):
    return datetime.now(UTC) - timedelta(seconds=seconds)


def worker_lock(connection):
    return connection.scalar(text("SELECT pg_try_advisory_lock(100045)"))


def active_user(db, identifier):
    return db.scalar(
        select(User).where(
            User.id == identifier, User.deleted_at.is_(None), User.is_active.is_(True)
        )
    )


def connection(db, identifier):
    return db.scalar(
        select(ServiceDatabase)
        .where(ServiceDatabase.id == identifier, ServiceDatabase.deleted_at.is_(None))
        .execution_options(access_internal=True)
        .execution_options(populate_existing=True)
    )


def configuration(db, identifier, *, lock=False):
    statement = (
        select(AskDataConfiguration)
        .where(AskDataConfiguration.connection_id == identifier)
        .execution_options(access_internal=True, populate_existing=True)
    )
    if lock:
        statement = statement.with_for_update()
    return db.scalar(statement)


def configurations(db, identifiers):
    return db.scalars(
        select(AskDataConfiguration)
        .where(AskDataConfiguration.connection_id.in_(identifiers))
        .execution_options(access_internal=True)
    ).all()


def model_version(db, identifier):
    return db.scalar(
        select(AskDataModelVersion)
        .where(AskDataModelVersion.id == identifier, AskDataModelVersion.deleted_at.is_(None))
        .execution_options(access_internal=True)
        .execution_options(populate_existing=True)
    )


def conversation(db, identifier, owner_id):
    return db.scalar(
        select(AskDataConversation).where(
            AskDataConversation.id == identifier,
            AskDataConversation.owner_id == owner_id,
            AskDataConversation.deleted_at.is_(None),
        )
    )


def run(db, identifier, owner_id):
    return db.scalar(
        select(AskDataRun)
        .join(AskDataConversation, AskDataConversation.id == AskDataRun.conversation_id)
        .where(
            AskDataRun.id == identifier,
            AskDataRun.deleted_at.is_(None),
            AskDataConversation.owner_id == owner_id,
            AskDataConversation.deleted_at.is_(None),
        )
    )


def save(db, row):
    db.add(row)


def execute(db, statement, parameters=None):
    return db.execute(statement.execution_options(access_internal=True), parameters or {})
