"""Database access; transaction ownership stays with the caller."""

from uuid import uuid4

from sqlalchemy import select

from app.models import (
    AlertEvent,
    Approval,
    AuditLog,
    Incident,
    IncidentAlert,
    IncidentTimeline,
    User,
)
from app.repositories.users import active_users


def locked_record_query(db, incident):
    db.refresh(incident, with_for_update=True)


def incidents_query():
    query = select(Incident).where(Incident.deleted_at.is_(None))
    return query


def incidents_query_2(field, query, value):
    query = query.where(getattr(Incident, field) == value)
    return query


def incidents_query_3(query, start):
    query = query.where(Incident.started_at >= start)
    return query


def incidents_query_4(end, query):
    query = query.where(Incident.started_at <= end)
    return query


def incidents_query_5(field, query, value):
    members = (
        select(IncidentAlert.incident_id)
        .join(AlertEvent, AlertEvent.id == IncidentAlert.alert_event_id)
        .where(IncidentAlert.deleted_at.is_(None), AlertEvent.deleted_at.is_(None))
    )
    members = members.where(getattr(AlertEvent, field) == value)
    query = query.where(Incident.id.in_(members))
    return members, query


def incidents_query_6(query):
    query = query.where(Incident.assignee_id.is_(None))
    return query


def incidents_query_7(query):
    return query.order_by(Incident.started_at.desc(), Incident.id)


def assignees_query():
    from app.repositories.access import capability_condition

    return (
        active_users()
        .where(User.is_active.is_(True), capability_condition("incidents:handle"))
        .order_by(User.username)
    )


def detail_query(db, incident_id):
    timeline = db.scalars(
        select(IncidentTimeline)
        .where(IncidentTimeline.incident_id == incident_id, IncidentTimeline.deleted_at.is_(None))
        .order_by(
            IncidentTimeline.occurred_at.desc(),
            IncidentTimeline.created_at.desc(),
            IncidentTimeline.id.desc(),
        )
        .limit(50)
    ).all()
    return timeline


def alerts_query(incident_id):
    return (
        select(AlertEvent)
        .join(IncidentAlert, IncidentAlert.alert_event_id == AlertEvent.id)
        .where(
            IncidentAlert.incident_id == incident_id,
            IncidentAlert.deleted_at.is_(None),
            AlertEvent.deleted_at.is_(None),
        )
        .order_by(AlertEvent.starts_at, AlertEvent.id)
    )


def timeline_query(incident_id):
    return (
        select(IncidentTimeline)
        .where(IncidentTimeline.incident_id == incident_id, IncidentTimeline.deleted_at.is_(None))
        .order_by(IncidentTimeline.occurred_at, IncidentTimeline.created_at, IncidentTimeline.id)
    )


def update_query(body, db):
    from app.repositories.access import capability_condition

    assignee = db.scalar(
        active_users()
        .where(
            User.id == body.assignee_id,
            User.is_active.is_(True),
            capability_condition("incidents:handle"),
        )
        .with_for_update(of=User)
    )
    return assignee


def update_query_2(db, incident):
    db.refresh(incident)


def status_query(db, incident):
    return db.scalars(
        select(Approval)
        .where(
            Approval.incident_id == incident.id,
            Approval.deleted_at.is_(None),
            Approval.status.in_(("pending", "approved")),
        )
        .with_for_update()
    )


def status_query_2(action, db, incident, request_ip, user):
    db.add(
        AuditLog(
            user_id=user.id,
            incident_id=incident.id,
            action=action,
            request_id=str(uuid4()),
            ip=request_ip,
            detail=incident.closure,
        )
    )


def status_query_3(db, incident):
    db.refresh(incident)


def comment_query(db, incident):
    previous = db.scalars(
        select(IncidentTimeline)
        .where(
            IncidentTimeline.incident_id == incident.id,
            IncidentTimeline.deleted_at.is_(None),
        )
        .order_by(IncidentTimeline.created_at.desc(), IncidentTimeline.id.desc())
        .limit(1)
    ).first()
    return previous


def comment_query_2(db, row):
    db.add(row)


def comment_query_3(db, row):
    db.refresh(row)


def merge_query(db, target):
    db.refresh(target, with_for_update=True)


def merge_query_2(db, source):
    members = db.scalars(
        select(IncidentAlert)
        .where(IncidentAlert.incident_id == source.id, IncidentAlert.deleted_at.is_(None))
        .with_for_update()
    ).all()
    return members


def merge_query_3(db, target):
    db.refresh(target)
