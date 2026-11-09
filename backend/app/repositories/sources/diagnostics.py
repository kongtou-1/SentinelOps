"""Database access; transaction ownership stays with the caller."""

from uuid import UUID

from sqlalchemy import or_, select

from app.models import (
    ApplicationDeployment,
    ApplicationRelease,
    Container,
    ServiceMonitor,
    SourceSnapshot,
)


def monitor_target(db, alert):
    row = db.get(ServiceMonitor, alert.service_monitor_id) if alert.service_monitor_id else None
    return row.container_id if row and not row.deleted_at else None


def freeze_source_context_query(db, target_id):
    container = db.get(Container, target_id) if target_id else None
    return container


def freeze_source_context_query_2(alert, db, docker_id):
    matches = db.scalars(
        select(Container)
        .where(
            Container.server_id == alert.server_id,
            or_(
                Container.docker_id == docker_id,
                Container.docker_id.like("%/" + docker_id),
                Container.docker_id.like("%-" + docker_id + ".scope"),
            ),
        )
        .limit(2)
    ).all()
    return matches


def freeze_source_context_query_3(alert, db):
    candidates = db.scalars(
        select(ApplicationDeployment)
        .where(
            ApplicationDeployment.server_id == alert.server_id,
            ApplicationDeployment.started_at <= alert.starts_at,
            or_(
                ApplicationDeployment.ended_at.is_(None),
                ApplicationDeployment.ended_at > alert.starts_at,
            ),
            ApplicationDeployment.deleted_at.is_(None),
        )
        .order_by(ApplicationDeployment.id)
        .limit(20)
    )
    return candidates


def freeze_source_context_query_4(alert, column, db, target_id):
    deployments = db.scalars(
        select(ApplicationDeployment)
        .where(
            column == target_id,
            ApplicationDeployment.started_at <= alert.starts_at,
            or_(
                ApplicationDeployment.ended_at.is_(None),
                ApplicationDeployment.ended_at > alert.starts_at,
            ),
            ApplicationDeployment.deleted_at.is_(None),
        )
        .limit(2)
    ).all()
    return deployments


def freeze_source_context_query_5(db, deployment):
    release = db.get(ApplicationRelease, deployment.release_id)
    return release


def freeze_source_context_add_snapshot_query(db, identifier):
    source = db.get(SourceSnapshot, identifier) if identifier else None
    return source


def freeze_source_context_query_6(db, deployment):
    reference = db.scalar(
        select(SourceSnapshot)
        .where(
            SourceSnapshot.application_id == deployment.application_id,
            SourceSnapshot.status.in_(("ready", "partial")),
            SourceSnapshot.deleted_at.is_(None),
        )
        .order_by(SourceSnapshot.created_at.desc(), SourceSnapshot.id)
        .limit(1)
    )
    return reference


def freeze_source_context_query_7(column, db, deployment, target_id):
    previous = db.scalar(
        select(ApplicationDeployment)
        .where(
            column == target_id,
            ApplicationDeployment.application_id == deployment.application_id,
            ApplicationDeployment.ended_at <= deployment.started_at,
            ApplicationDeployment.deleted_at.is_(None),
        )
        .order_by(ApplicationDeployment.ended_at.desc(), ApplicationDeployment.id)
        .limit(1)
    )
    return previous


def freeze_source_context_query_8(db, previous):
    prior_release = db.get(ApplicationRelease, previous.release_id)
    return prior_release


def source_evidence_valid_query(db, identity):
    row = db.get(SourceSnapshot, UUID(identity["snapshot_id"]))
    return row


def dispatch_source_query(db, identity):
    source = db.get(SourceSnapshot, UUID(identity["snapshot_id"]))
    return source


def dispatch_source_query_2(body, db):
    return db.get(SourceSnapshot, body.snapshot_id)
