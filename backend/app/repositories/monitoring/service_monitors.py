from sqlalchemy import or_, select

from app.models import (
    AlertEvent,
    Application,
    Container,
    Incident,
    IncidentAlert,
    LLMEndpoint,
    LLMServiceBinding,
    Server,
    ServiceMonitor,
)


def incident_monitors(db, incident_id):
    return (
        db.scalars(
            select(ServiceMonitor)
            .join(AlertEvent, AlertEvent.service_monitor_id == ServiceMonitor.id)
            .join(IncidentAlert, IncidentAlert.alert_event_id == AlertEvent.id)
            .where(
                IncidentAlert.incident_id == incident_id,
                IncidentAlert.deleted_at.is_(None),
                AlertEvent.deleted_at.is_(None),
                ServiceMonitor.deleted_at.is_(None),
            )
        )
        .unique()
        .all()
    )


def incident_assets(db, monitor):
    application = db.get(Application, monitor.application_id)
    ids = [monitor.application_id]
    if application and not application.deleted_at:
        from uuid import UUID

        ids.extend(UUID(str(v)) for v in application.depends_on)
    containers = db.scalars(
        select(Container)
        .join(Application)
        .where(
            Container.application_id.in_(ids),
            Container.deleted_at.is_(None),
            Application.deleted_at.is_(None),
        )
    ).all()
    endpoints = (
        db.scalars(
            select(LLMEndpoint)
            .join(LLMServiceBinding)
            .join(Application, Application.id == LLMServiceBinding.application_id)
            .where(
                LLMServiceBinding.application_id.in_(ids),
                LLMServiceBinding.deleted_at.is_(None),
                LLMEndpoint.deleted_at.is_(None),
                Application.deleted_at.is_(None),
            )
        )
        .unique()
        .all()
    )
    return containers, endpoints


def monitors(db, application_id=None):
    query = select(ServiceMonitor).where(ServiceMonitor.deleted_at.is_(None))
    if application_id:
        query = query.where(ServiceMonitor.application_id == application_id)
    return db.scalars(query.order_by(ServiceMonitor.created_at, ServiceMonitor.id)).all()


def active_monitors(db):
    return db.scalars(
        select(ServiceMonitor)
        .join(Application)
        .where(
            ServiceMonitor.enabled.is_(True),
            ServiceMonitor.deleted_at.is_(None),
            Application.enabled.is_(True),
            Application.deleted_at.is_(None),
        )
    ).all()


def add(db, row):
    db.add(row)


def current(db, identifier):
    return db.get(ServiceMonitor, identifier)


def applications(db):
    return db.scalars(select(Application).where(Application.deleted_at.is_(None))).all()


def containers(db, application_id):
    return db.scalars(
        select(Container).where(
            Container.application_id == application_id, Container.deleted_at.is_(None)
        )
    ).all()


def server_containers(db, server_id):
    return db.scalars(
        select(Container).where(Container.server_id == server_id, Container.deleted_at.is_(None))
    ).all()


def server(db, identifier):
    return db.get(Server, identifier)


def endpoints(db, application_id):
    return db.scalars(
        select(LLMEndpoint)
        .join(LLMServiceBinding)
        .where(
            LLMServiceBinding.application_id == application_id,
            LLMServiceBinding.deleted_at.is_(None),
            LLMEndpoint.deleted_at.is_(None),
        )
    ).all()


def related_alerts(db, monitor_ids, container_ids, endpoint_ids):
    return db.scalars(
        select(AlertEvent)
        .where(
            AlertEvent.deleted_at.is_(None),
            or_(
                AlertEvent.service_monitor_id.in_(monitor_ids),
                AlertEvent.labels["container_id"].as_string().in_([str(v) for v in container_ids]),
                AlertEvent.llm_endpoint_id.in_(endpoint_ids),
            ),
        )
        .order_by(AlertEvent.starts_at.desc())
        .limit(50)
    ).all()


def incidents(db, alert_ids):
    return (
        db.scalars(
            select(Incident)
            .join(IncidentAlert)
            .where(
                IncidentAlert.alert_event_id.in_(alert_ids),
                IncidentAlert.deleted_at.is_(None),
                Incident.deleted_at.is_(None),
            )
            .order_by(Incident.started_at.desc())
            .limit(50)
        )
        .unique()
        .all()
    )


def traffic_applications(db):
    return db.scalars(
        select(Application).where(
            Application.deleted_at.is_(None),
            Application.enabled.is_(True),
            Application.traffic_target.is_not(None),
        )
    ).all()
