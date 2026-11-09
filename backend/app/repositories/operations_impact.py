from datetime import timedelta

from sqlalchemy import or_, select

from app.models import (
    AlertEvent,
    Application,
    Container,
    Incident,
    IncidentAlert,
    IncidentRelation,
    LLMEndpoint,
    LLMServiceBinding,
    PredictionTask,
    ServiceMonitor,
    ServiceOperationsPolicy,
)


def policy(db, application_id, lock=False):
    statement = select(ServiceOperationsPolicy).where(
        ServiceOperationsPolicy.application_id == application_id,
        ServiceOperationsPolicy.deleted_at.is_(None),
    )
    return db.scalar(statement.with_for_update() if lock else statement)


def applications(db):
    return db.scalars(
        select(Application).where(Application.deleted_at.is_(None), Application.enabled.is_(True))
    ).all()


def monitors(db):
    return db.scalars(select(ServiceMonitor).where(ServiceMonitor.deleted_at.is_(None))).all()


def containers(db):
    return db.scalars(select(Container).where(Container.deleted_at.is_(None))).all()


def endpoint_bindings(db):
    return db.execute(
        select(LLMServiceBinding, LLMEndpoint)
        .join(LLMEndpoint)
        .where(LLMServiceBinding.deleted_at.is_(None), LLMEndpoint.deleted_at.is_(None))
    ).all()


def endpoint_applications(db, endpoint_id):
    return set(
        db.scalars(
            select(LLMServiceBinding.application_id).where(
                LLMServiceBinding.llm_endpoint_id == endpoint_id,
                LLMServiceBinding.deleted_at.is_(None),
            )
        )
    )


def server_applications(db, server_id):
    return set(
        db.scalars(
            select(Container.application_id).where(
                Container.server_id == server_id,
                Container.application_id.is_not(None),
                Container.deleted_at.is_(None),
            )
        )
    )


def event_applications(db, event):
    if event.service_monitor_id:
        monitor = db.get(ServiceMonitor, event.service_monitor_id)
        return {monitor.application_id} if monitor else set()
    if event.llm_endpoint_id:
        return endpoint_applications(db, event.llm_endpoint_id)
    container_id = (event.labels or {}).get("container_id")
    if event.prediction_task_id:
        task = db.get(PredictionTask, event.prediction_task_id)
        container_id = task.container_id if task else container_id
    if container_id:
        from uuid import UUID

        try:
            container = db.get(Container, UUID(str(container_id)))
        except ValueError:
            container = None
        if container and container.application_id:
            return {container.application_id}
    return server_applications(db, event.server_id) if event.server_id else set()


def incident_events(db, incident_id):
    return db.scalars(
        select(AlertEvent)
        .join(IncidentAlert)
        .where(
            IncidentAlert.incident_id == incident_id,
            IncidentAlert.deleted_at.is_(None),
            AlertEvent.deleted_at.is_(None),
        )
    ).all()


def recent_incidents(db, incident, recurrence=False):
    statement = select(Incident).where(
        Incident.id != incident.id,
        Incident.deleted_at.is_(None),
    )
    if recurrence:
        statement = statement.where(
            Incident.status.in_(("recovered", "closed")),
            Incident.recovered_at.is_not(None),
            Incident.recovered_at <= incident.started_at,
            Incident.recovered_at >= incident.started_at - timedelta(minutes=30),
        ).order_by(Incident.recovered_at.desc())
    else:
        statement = statement.where(
            Incident.status.in_(("open", "in_progress")),
            Incident.last_alert_started_at >= incident.started_at - timedelta(minutes=5),
            Incident.started_at <= incident.last_alert_started_at + timedelta(minutes=5),
        ).order_by(Incident.created_at.desc())
    return db.scalars(statement.limit(100)).all()


def relations(incident_id):
    return (
        select(IncidentRelation)
        .where(
            or_(
                IncidentRelation.incident_id == incident_id,
                IncidentRelation.related_incident_id == incident_id,
            ),
            IncidentRelation.deleted_at.is_(None),
        )
        .order_by(IncidentRelation.created_at.desc())
    )


def existing_relation(db, incident_id, related_id, kind):
    return db.scalar(
        select(IncidentRelation).where(
            IncidentRelation.incident_id == incident_id,
            IncidentRelation.related_incident_id == related_id,
            IncidentRelation.kind == kind,
        )
    )


def relation_for_update(db, identifier):
    return db.scalar(
        select(IncidentRelation)
        .where(IncidentRelation.id == identifier, IncidentRelation.deleted_at.is_(None))
        .with_for_update()
    )
