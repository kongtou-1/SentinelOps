"""Database access; transaction ownership stays with the caller."""

from sqlalchemy import select

from app.models import AuditLog, Incident, User


def incident_rows_query(incident_id, model):
    return (
        select(model)
        .where(model.incident_id == incident_id, model.deleted_at.is_(None))
        .order_by(model.created_at.desc(), model.id.desc())
    )


def audits_query():
    query = select(AuditLog).where(AuditLog.deleted_at.is_(None))
    return query


def audits_query_2(field, query, value):
    query = query.where(getattr(AuditLog, field) == value)
    return query


def audits_query_3(action, query):
    query = query.where(AuditLog.action == action)
    return query


def audits_query_4(query):
    return query.order_by(AuditLog.created_at.desc(), AuditLog.id.desc())


def audit_incident_search(query, title):
    return query.where(
        AuditLog.incident_id.in_(
            select(Incident.id).where(Incident.title.icontains(title, autoescape=True))
        )
    )


def audit_names(db, rows):
    # Keep historical names available after a linked record has been soft-deleted.
    users = dict(
        db.execute(
            select(User.id, User.username).where(
                User.id.in_({row.user_id for row in rows if row.user_id})
            )
        ).all()
    )
    incidents = dict(
        db.execute(
            select(Incident.id, Incident.title).where(
                Incident.id.in_({row.incident_id for row in rows})
            )
        ).all()
    )
    return users, incidents
