from sqlalchemy import or_, select

from app.models import Application, ServiceDatabase, SourceAuditLog
from app.repositories.notifications import audit as operations_audit


def listing(db, application_id=None):
    query = (
        select(ServiceDatabase)
        .outerjoin(Application)
        .where(
            ServiceDatabase.deleted_at.is_(None),
            or_(ServiceDatabase.application_id.is_(None), Application.deleted_at.is_(None)),
        )
        .order_by(ServiceDatabase.created_at, ServiceDatabase.id)
    )
    if application_id is not None:
        query = query.where(ServiceDatabase.application_id == application_id)
    return db.scalars(query).all()


def current(db, identifier):
    return db.get(ServiceDatabase, identifier, populate_existing=True)


def application(db, identifier):
    # The caller authorizes the connection; this parent lookup only checks lifecycle.
    return db.scalar(
        select(Application)
        .where(Application.id == identifier)
        .execution_options(access_internal=True)
    )


def save(db, row):
    db.add(row)


def audit(db, row, user, action):
    if row.application_id is None:
        operations_audit(
            db,
            user,
            action,
            "service_database",
            row.id,
            {"connection_id": str(row.id), "config_version": row.config_version},
        )
        return
    db.add(
        SourceAuditLog(
            application_id=row.application_id,
            user_id=user.id,
            action=action,
            detail={"connection_id": str(row.id), "config_version": row.config_version},
        )
    )
