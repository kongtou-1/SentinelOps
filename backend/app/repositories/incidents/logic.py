"""Database access; transaction ownership stays with the caller."""

from sqlalchemy import select, text

from app.models import (
    AlertEvent,
    Incident,
    IncidentAlert,
)


def lock_incidents_query(db):
    db.execute(text("SELECT pg_advisory_xact_lock(7072026)"))


def append_timeline_query(db, row):
    db.add(row)


def linked_alerts_query(db, incident_id):
    return db.scalars(
        select(AlertEvent)
        .join(IncidentAlert, IncidentAlert.alert_event_id == AlertEvent.id)
        .where(
            IncidentAlert.incident_id == incident_id,
            IncidentAlert.deleted_at.is_(None),
            AlertEvent.deleted_at.is_(None),
        )
    )


def event_incident_query(db, event):
    return db.scalar(
        select(Incident)
        .join(IncidentAlert, IncidentAlert.incident_id == Incident.id)
        .where(
            IncidentAlert.alert_event_id == event.id,
            IncidentAlert.deleted_at.is_(None),
            Incident.deleted_at.is_(None),
        )
        .with_for_update(of=Incident)
    )


def valid_asset_query(db, model, record_id):
    record = db.get(model, record_id)
    return record


def attach_alert_query(db):
    db.flush()


def attach_alert_query_2(column, db, record_id):
    candidates = (
        db.scalars(
            select(Incident)
            .join(IncidentAlert, IncidentAlert.incident_id == Incident.id)
            .join(AlertEvent, AlertEvent.id == IncidentAlert.alert_event_id)
            .where(
                Incident.status.in_(("open", "in_progress")),
                Incident.deleted_at.is_(None),
                IncidentAlert.deleted_at.is_(None),
                AlertEvent.deleted_at.is_(None),
                getattr(AlertEvent, column) == record_id,
            )
        )
        .unique()
        .all()
    )
    return candidates


def attach_alert_query_3(db, incident):
    db.add(incident)
    db.flush()


def attach_alert_query_4(db, event, incident):
    db.add(IncidentAlert(incident_id=incident.id, alert_event_id=event.id))


def attach_alert_query_5(db, event):
    db.flush()
    db.expire(event, ["incident_link"])


def recompute_query(db):
    db.flush()
