"""Database access; transaction ownership stays with the caller."""

from uuid import uuid4

from sqlalchemy import select

from app.models import (
    AuditLog,
    Incident,
    IncidentTimeline,
    PostmortemDelivery,
    PostmortemReport,
    PostmortemRevision,
)


def report_audit_query(action, content, db, report, request_ip, user):
    db.add(
        AuditLog(
            user_id=user.id if user else None,
            incident_id=report.incident_id,
            report_id=report.id,
            action=action,
            request_id=str(uuid4()),
            ip=request_ip,
            detail=content,
        )
    )


def report_audit_query_2(db, report):
    return db.get(Incident, report.incident_id)


def source_incidents_query(db, pending, row):
    pending.extend(
        db.scalars(
            select(Incident).where(Incident.merged_into_id == row.id, Incident.deleted_at.is_(None))
        ).all()
    )


def complete_incident_query(db, ids, model, statuses):
    return db.scalar(
        select(model.id)
        .where(model.incident_id.in_(ids), model.status.in_(statuses), model.deleted_at.is_(None))
        .limit(1)
    )


def snapshot_query(ids, model):
    query = select(model).where(model.incident_id.in_(ids), model.deleted_at.is_(None))
    return query


def snapshot_query_2(query):
    query = query.where(AuditLog.report_id.is_(None))
    return query


def snapshot_query_3(query):
    query = query.where(~IncidentTimeline.event_type.like("report_%"))
    return query


def snapshot_query_4(db, model, query):
    return db.scalars(query.order_by(model.created_at, model.id))


def generate_query(db, incident_id):
    existing = db.scalar(
        select(PostmortemReport)
        .where(
            PostmortemReport.incident_id == incident_id,
            PostmortemReport.deleted_at.is_(None),
            PostmortemReport.status.in_(("queued", "running")),
        )
        .limit(1)
    )
    return existing


def generate_query_2(db, incident_id):
    existing = db.scalar(
        select(PostmortemReport)
        .where(
            PostmortemReport.incident_id == incident_id,
            PostmortemReport.deleted_at.is_(None),
            PostmortemReport.status.in_(("draft", "approved")),
        )
        .order_by(PostmortemReport.created_at.desc(), PostmortemReport.id.desc())
        .limit(1)
    )
    return existing


def generate_query_3(db, row):
    db.add(row)
    db.flush()


def generate_query_4(db, row):
    db.refresh(row)


def locked_report_query(db, row):
    db.refresh(row, with_for_update=True)


def delete_report_query(db, row):
    active_delivery = db.scalar(
        select(PostmortemDelivery.id)
        .where(
            PostmortemDelivery.report_id == row.id,
            PostmortemDelivery.deleted_at.is_(None),
            PostmortemDelivery.status.in_(("queued", "sending")),
        )
        .limit(1)
    )
    return active_delivery


def revision_query(db, row):
    return db.scalar(
        select(PostmortemRevision).where(
            PostmortemRevision.report_id == row.id,
            PostmortemRevision.revision == row.revision,
            PostmortemRevision.deleted_at.is_(None),
        )
    )


def add_revision_query(content, db, content_hash, row, user):
    return db.add(
        PostmortemRevision(
            report_id=row.id,
            revision=row.revision,
            author_id=user.id if user else None,
            content=content,
            content_hash=content_hash,
        )
    )


def edit_query(db, row):
    db.refresh(row)


def review_query(db, row):
    db.refresh(row)


def approved_export_query(db, row):
    frozen = db.scalar(
        select(PostmortemDelivery.attachment)
        .where(
            PostmortemDelivery.report_id == row.id,
            PostmortemDelivery.revision == row.revision,
            PostmortemDelivery.deleted_at.is_(None),
        )
        .order_by(PostmortemDelivery.created_at, PostmortemDelivery.id)
        .limit(1)
    )
    return frozen


def enqueue_delivery_query(body, db):
    previous = db.scalar(
        select(PostmortemDelivery).where(PostmortemDelivery.idempotency_key == body.idempotency_key)
    )
    return previous


def enqueue_delivery_query_2(db, delivery):
    db.add(delivery)
    db.flush()


def enqueue_delivery_query_3(db, delivery):
    db.refresh(delivery)
