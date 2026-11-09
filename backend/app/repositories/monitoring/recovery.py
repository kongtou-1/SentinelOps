"""Database access; transaction ownership stays with the caller."""

from uuid import UUID

from sqlalchemy import case, or_, select

from app.models import (
    AlertEvent,
    AlertRule,
    Container,
    Incident,
    IncidentAlert,
    LLMEndpoint,
    PredictionTask,
    Server,
)


def evidence_query(db, identifier, model):
    record = db.get(model, identifier)
    return record


def evidence_query_2(db, event):
    rule = db.get(AlertRule, event.rule_id) if event.rule_id else None
    return rule


def evidence_query_3(db, event):
    task = db.get(PredictionTask, event.prediction_task_id) if event.prediction_task_id else None
    return task


def evidence_query_4(db, event):
    endpoint = db.get(LLMEndpoint, event.llm_endpoint_id) if event.llm_endpoint_id else None
    server = db.get(Server, event.server_id) if event.server_id else None
    return endpoint, server


def evidence_query_5(db, event):
    container = db.get(Container, UUID(event.labels["container_id"]))
    return container


def evidence_query_6(db, event):
    containers = db.scalars(select(Container).where(Container.server_id == event.server_id)).all()
    return containers


def reconcile_query(db, limit):
    pending = (
        select(AlertEvent.id)
        .join(IncidentAlert, IncidentAlert.alert_event_id == AlertEvent.id)
        .join(Incident, Incident.id == IncidentAlert.incident_id)
        .where(
            AlertEvent.status == "resolved",
            AlertEvent.recovery_status == "pending",
            AlertEvent.deleted_at.is_(None),
            IncidentAlert.deleted_at.is_(None),
            Incident.status.in_(("open", "in_progress")),
            Incident.deleted_at.is_(None),
            # Polling cannot manufacture a binding for old, unbound notifications.
            or_(
                AlertEvent.rule_snapshot["id"].as_string().is_not(None),
                AlertEvent.rule_id.is_not(None),
                AlertEvent.server_id.is_not(None),
                AlertEvent.llm_endpoint_id.is_not(None),
                AlertEvent.prediction_task_id.is_not(None),
            ),
        )
    )
    # Reserve half the bounded batch for recently ended, verifiable occurrences,
    # including retries after missing evidence. Keep the remainder for the oldest
    # unchecked/rechecked backlog so recent failures cannot monopolize the worker.
    recent = (
        db.scalars(
            pending.where(AlertEvent.rule_snapshot["id"].as_string().is_not(None))
            .order_by(AlertEvent.ends_at.desc(), AlertEvent.starts_at.desc())
            .limit(limit // 2)
        ).all()
        if limit > 1
        else []
    )
    oldest = db.scalars(
        pending.order_by(
            case((AlertEvent.rule_snapshot["id"].as_string().is_not(None), 0), else_=1),
            AlertEvent.recovery_checked_at.asc().nullsfirst(),
            AlertEvent.starts_at.desc(),
        ).limit(limit)
    ).all()
    return oldest, recent


def reconcile_query_2(db, identifier):
    event = db.get(AlertEvent, identifier)
    return event


def reconcile_query_3(db, identifier):
    event = db.get(AlertEvent, identifier, populate_existing=True, with_for_update=True)
    return event


def reconcile_query_4(db, event):
    rule = db.get(AlertRule, event.rule_id, populate_existing=True, with_for_update=True)
    return rule


def reconcile_query_5(db, event):
    server = (
        db.get(Server, event.server_id, populate_existing=True, with_for_update=True)
        if event.server_id
        else None
    )
    endpoint = (
        db.get(LLMEndpoint, event.llm_endpoint_id, populate_existing=True, with_for_update=True)
        if event.llm_endpoint_id
        else None
    )
    return endpoint, server


def reconcile_query_6(db, event):
    task = db.get(
        PredictionTask,
        event.prediction_task_id,
        populate_existing=True,
        with_for_update=True,
    )
    return task
