"""Database access; transaction ownership stays with the caller."""

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert

from app.models import (
    AlertEvent,
    AlertRule,
    IncidentAlert,
)


def rules_query():
    return select(AlertRule).where(AlertRule.deleted_at.is_(None)).order_by(AlertRule.code)


def delete_rule_query():
    return func.clock_timestamp()


def active_reference_query(db, model, record_id):
    return db.scalar(select(model.id).where(model.id == record_id, model.deleted_at.is_(None)))


def webhook_query(db, values):
    stmt = insert(AlertEvent).values(**values)
    event_id = db.scalar(
        stmt.on_conflict_do_nothing(constraint="uq_alert_events_fingerprint").returning(
            AlertEvent.id
        )
    )
    return event_id


def webhook_query_2(alert, db, values):
    event = db.scalar(
        select(AlertEvent)
        .where(
            AlertEvent.fingerprint == values["fingerprint"],
            AlertEvent.starts_at == alert.startsAt,
            AlertEvent.deleted_at.is_(None),
        )
        .with_for_update()
    )
    return event


def alerts_query():
    query = select(AlertEvent).where(AlertEvent.deleted_at.is_(None))
    return query


def alerts_query_2(query, status):
    query = query.where(AlertEvent.status == status)
    return query


def alerts_query_3(query, server_id):
    query = query.where(AlertEvent.server_id == server_id)
    return query


def alerts_query_4(llm_endpoint_id, query):
    query = query.where(AlertEvent.llm_endpoint_id == llm_endpoint_id)
    return query


def alerts_query_5(prediction_task_id, query):
    query = query.where(AlertEvent.prediction_task_id == prediction_task_id)
    return query


def alerts_query_6(incident_id, query):
    query = query.join(IncidentAlert, IncidentAlert.alert_event_id == AlertEvent.id).where(
        IncidentAlert.incident_id == incident_id, IncidentAlert.deleted_at.is_(None)
    )
    return query


def alerts_query_7(query, severity):
    query = query.where(AlertEvent.severity == severity)
    return query


def alerts_query_8(acknowledged, query):
    query = query.where(
        AlertEvent.acknowledged_at.is_not(None)
        if acknowledged
        else AlertEvent.acknowledged_at.is_(None)
    )
    return query


def alerts_query_9(query):
    return query.order_by(AlertEvent.starts_at.desc())


def acknowledge_query(db, event):
    db.refresh(event, with_for_update=True)


def acknowledge_query_2():
    return func.clock_timestamp()


def acknowledge_query_3(db):
    db.flush()


def acknowledge_query_4(db, event):
    db.refresh(event)
