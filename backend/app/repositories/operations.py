from datetime import timedelta

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert

from app.models import ServiceObjective, SourceAuditLog, WorkerHeartbeat


def objectives(application_id):
    return (
        select(ServiceObjective)
        .where(
            ServiceObjective.application_id == application_id, ServiceObjective.deleted_at.is_(None)
        )
        .order_by(ServiceObjective.created_at, ServiceObjective.id)
    )


def objective_for_update(db, identifier):
    return db.scalar(
        select(ServiceObjective)
        .where(ServiceObjective.id == identifier, ServiceObjective.deleted_at.is_(None))
        .with_for_update()
    )


def due_objectives(db, now, limit=5):
    return db.scalars(
        select(ServiceObjective)
        .where(
            ServiceObjective.enabled.is_(True),
            ServiceObjective.deleted_at.is_(None),
            (ServiceObjective.checked_at.is_(None))
            | (ServiceObjective.checked_at < now - timedelta(seconds=60)),
        )
        .order_by(ServiceObjective.checked_at.asc().nullsfirst(), ServiceObjective.id)
        .limit(limit)
    ).all()


def heartbeat(db, name, now, state="idle", detail=None):
    statement = insert(WorkerHeartbeat).values(
        id=name, observed_at=now, state=state, detail=detail or {}
    )
    db.execute(
        statement.on_conflict_do_update(
            index_elements=[WorkerHeartbeat.id],
            set_={"observed_at": now, "state": state, "detail": detail or {}},
        )
    )


def worker_lock(connection):
    return connection.scalar(text("SELECT pg_try_advisory_lock(100025)"))


def worker_alive(connection):
    connection.execute(text("SELECT 1"))


def service_audit(db, application_id, actor, action, detail):
    db.add(
        SourceAuditLog(
            application_id=application_id, user_id=actor.id, action=action, detail=detail
        )
    )
