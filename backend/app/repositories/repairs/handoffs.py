"""Database access; transaction ownership stays with the caller."""

from uuid import UUID, uuid4

from app.models import (
    ApplicationDeployment,
    AuditLog,
    Incident,
    SourceSnapshot,
)


def audit_query(action, content, db, detail, request_ip, row, user):
    return db.add(
        AuditLog(
            user_id=user.id,
            incident_id=row.incident_id,
            action=action,
            request_id=str(uuid4()),
            ip=request_ip,
            detail=detail,
        )
    )


def audit_query_2(db, row):
    return db.get(Incident, row.incident_id)


def create_query(binding, db):
    deployment = db.get(ApplicationDeployment, UUID(binding["deployment_id"]))
    return deployment


def create_query_2(db, row):
    db.add(row)
    db.flush()


def create_query_3(db, row):
    db.refresh(row)


def locked_query(db, row):
    db.refresh(row, with_for_update=True)


def record_result_query(db, release):
    source = (
        db.get(SourceSnapshot, release.snapshot_id) if release and release.snapshot_id else None
    )
    return source


def record_result_query_2(db, row):
    db.refresh(row)


def verify_query(db, row):
    db.refresh(row)
