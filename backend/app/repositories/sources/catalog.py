"""Database access; transaction ownership stays with the caller."""

from sqlalchemy import or_, select

from app.models import (
    Application,
    ApplicationDeployment,
    ApplicationRelease,
    Container,
    LLMEndpoint,
    SourceAuditLog,
    SourceSnapshot,
)
from app.utils.redaction import redact


def audit_query(action, application_id, db, detail, user):
    db.add(
        SourceAuditLog(
            application_id=application_id,
            user_id=user.id,
            action=action,
            detail=redact(detail),
        )
    )


def applications_query(q):
    return (
        select(Application)
        .where(
            Application.deleted_at.is_(None),
            or_(
                Application.name.icontains(q, autoescape=True),
                Application.code.icontains(q, autoescape=True),
            ),
        )
        .order_by(Application.code)
    )


def create_application_query(db, row):
    db.add(row)


def update_application_query(db, row):
    db.refresh(row, with_for_update=True)


def delete_application_query(db, row):
    db.refresh(row, with_for_update=True)


def delete_application_query_2(db, row):
    return db.scalar(
        select(ApplicationDeployment.id)
        .where(
            ApplicationDeployment.application_id == row.id,
            ApplicationDeployment.ended_at.is_(None),
            ApplicationDeployment.deleted_at.is_(None),
        )
        .limit(1)
    )


def queue_snapshot_query(app, db):
    db.refresh(app, with_for_update=True)


def queue_snapshot_query_2(app, db):
    previous = db.scalars(
        select(SourceSnapshot).where(
            SourceSnapshot.application_id == app.id,
            SourceSnapshot.deleted_at.is_(None),
            SourceSnapshot.status.in_(("queued", "running")),
        )
    )
    return previous


def queue_snapshot_query_3(db, row):
    db.add(row)
    db.flush()


def snapshots_query(shared):
    return (
        select(SourceSnapshot)
        .where(
            or_(*shared),
            SourceSnapshot.deleted_at.is_(None),
        )
        .order_by(SourceSnapshot.created_at.desc(), SourceSnapshot.id)
    )


def create_release_query(app, db):
    db.refresh(app, with_for_update=True)


def create_release_query_2(app, body, db):
    return db.scalar(
        select(ApplicationRelease.id).where(
            ApplicationRelease.application_id == app.id,
            ApplicationRelease.version == body.version,
        )
    )


def create_release_query_3(db, row):
    db.add(row)


def releases_query(application_id):
    return (
        select(ApplicationRelease)
        .where(
            ApplicationRelease.application_id == application_id,
            ApplicationRelease.deleted_at.is_(None),
        )
        .order_by(ApplicationRelease.created_at.desc())
    )


def attach_snapshot_query(db, row):
    db.refresh(row, with_for_update=True)


def create_deployment_query(app, db):
    db.refresh(app, with_for_update=True)


def create_deployment_query_2(db, target):
    db.refresh(target, with_for_update=True)


def create_deployment_query_3(body, condition):
    overlap = select(ApplicationDeployment.id).where(
        condition,
        ApplicationDeployment.deleted_at.is_(None),
        or_(
            ApplicationDeployment.ended_at.is_(None),
            ApplicationDeployment.ended_at > body.started_at,
        ),
    )
    return overlap


def create_deployment_query_4(body, overlap):
    overlap = overlap.where(ApplicationDeployment.started_at < body.ended_at)
    return overlap


def create_deployment_query_5(db, overlap):
    return db.scalar(overlap.limit(1))


def create_deployment_query_6(db, release):
    selected = db.get(SourceSnapshot, release.snapshot_id)
    return selected


def deployments_query(application_id):
    return (
        select(ApplicationDeployment)
        .where(
            ApplicationDeployment.application_id == application_id,
            ApplicationDeployment.deleted_at.is_(None),
        )
        .order_by(ApplicationDeployment.started_at.desc())
    )


def end_deployment_query(db, row):
    target = db.get(
        Container if row.container_id else LLMEndpoint, row.container_id or row.llm_endpoint_id
    )
    db.refresh(target, with_for_update=True)
    db.refresh(row, with_for_update=True)


def container_deployments_query(container_id, db):
    rows = db.execute(
        select(ApplicationDeployment, Application, ApplicationRelease)
        .join(
            Application,
            Application.id == ApplicationDeployment.application_id,
        )
        .join(ApplicationRelease, ApplicationRelease.id == ApplicationDeployment.release_id)
        .where(
            ApplicationDeployment.container_id == container_id,
            ApplicationDeployment.deleted_at.is_(None),
            Application.deleted_at.is_(None),
            ApplicationRelease.deleted_at.is_(None),
        )
        .order_by(ApplicationDeployment.started_at.desc())
        .limit(50)
    ).all()
    return rows


def container_deployments_query_2(container_id, db):
    return db.get(Container, container_id)
