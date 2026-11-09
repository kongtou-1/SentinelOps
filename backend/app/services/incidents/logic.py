"""Incident writes share the webhook transaction and a single transaction-level lock."""

from datetime import UTC, datetime
from uuid import UUID

from fastapi import HTTPException

import app.repositories.incidents.logic as _repository
from app.models import (
    Container,
    Incident,
    IncidentTimeline,
    LLMEndpoint,
    PredictionTask,
    Server,
    ServiceMonitor,
)


def lock_incidents(db):
    # All incident writes acquire this before record locks, including alert acknowledgement.
    _repository.lock_incidents_query(db)


def writable(incident):
    if incident.status in ("closed", "merged"):
        raise HTTPException(409, "Closed or merged incidents are read-only")


def append_timeline(db, incident, event_type, content, occurred_at=None, actor=None):
    row = IncidentTimeline(
        incident_id=incident.id,
        event_type=event_type,
        occurred_at=occurred_at or datetime.now(UTC),
        actor_type="user" if actor else "system",
        actor_id=actor.id if actor else None,
        content=content,
    )
    _repository.append_timeline_query(db, row)
    return row


def linked_alerts(db, incident_id):
    return _repository.linked_alerts_query(db, incident_id).all()


def event_incident(db, event):
    return _repository.event_incident_query(db, event)


def valid_asset(db, model, record_id):
    if record_id is None:
        return None
    record = _repository.valid_asset_query(db, model, record_id)
    return record if record and record.deleted_at is None else None


def resolve_resource(db, event):
    if event.service_monitor_id:
        monitor = valid_asset(db, ServiceMonitor, event.service_monitor_id)
        event.server_id = event.llm_endpoint_id = None
        container = (
            valid_asset(db, Container, monitor.container_id)
            if monitor and monitor.container_id
            else None
        )
        if container and container.application_id == monitor.application_id:
            event.server_id = container.server_id
        return ("service_monitor_id", monitor.id) if monitor else None
    # Persist the prediction's actual asset on the AlertEvent for later filtering and merges.
    if event.prediction_task_id:
        task = valid_asset(db, PredictionTask, event.prediction_task_id)
        event.server_id = event.llm_endpoint_id = None
        if task:
            event.server_id = task.server_id
            event.llm_endpoint_id = task.llm_endpoint_id
            if task.container_id:
                container = valid_asset(db, Container, task.container_id)
                event.server_id = container.server_id if container else None
    elif event.labels.get("container_id"):
        try:
            container_id = UUID(event.labels["container_id"])
        except ValueError:
            container_id = None
        container = valid_asset(db, Container, container_id)
        if container:
            if event.server_id and event.server_id != container.server_id:
                event.server_id = event.llm_endpoint_id = None
                return None
            event.server_id = container.server_id
    server = valid_asset(db, Server, event.server_id)
    endpoint = valid_asset(db, LLMEndpoint, event.llm_endpoint_id)
    # Conflicting asset labels are not sufficient evidence to correlate incidents.
    if bool(server) == bool(endpoint):
        event.server_id = event.llm_endpoint_id = None
        return None
    event.server_id = server.id if server else None
    event.llm_endpoint_id = endpoint.id if endpoint else None
    return ("server_id", server.id) if server else ("llm_endpoint_id", endpoint.id)


def attach_alert(db, event):
    existing = event_incident(db, event)
    if existing:
        return existing
    resource = resolve_resource(db, event)
    _repository.attach_alert_query(db)
    candidates = []
    if resource:
        column, record_id = resource
        candidates = _repository.attach_alert_query_2(column, db, record_id)
        candidates = [
            item
            for item in candidates
            if abs((event.starts_at - item.last_alert_started_at).total_seconds()) <= 300
        ]
    incident = min(
        candidates,
        key=lambda item: (
            abs((event.starts_at - item.last_alert_started_at).total_seconds()),
            item.created_at,
            str(item.id),
        ),
        default=None,
    )
    created = incident is None
    if created:
        incident = Incident(
            title=(event.annotations.get("summary") or event.labels.get("alertname") or "Alert")[
                :200
            ],
            severity="critical" if event.severity == "critical" else "warning",
            status="open",
            started_at=event.starts_at,
            last_alert_started_at=event.starts_at,
        )
        _repository.attach_alert_query_3(db, incident)
        append_timeline(db, incident, "incident_created", {}, event.starts_at)
    _repository.attach_alert_query_4(db, event, incident)
    content = {
        "alert_event_id": str(event.id),
        "alertname": event.labels.get("alertname"),
        "severity": event.severity,
        "server_id": str(event.server_id) if event.server_id else None,
        "llm_endpoint_id": str(event.llm_endpoint_id) if event.llm_endpoint_id else None,
    }
    append_timeline(db, incident, "alert_created", content, event.starts_at)
    append_timeline(db, incident, "alert_linked", content)
    if event.acknowledged_at:
        row = append_timeline(
            db,
            incident,
            "alert_acknowledged",
            {"alert_event_id": str(event.id), "comment": event.comment, "backfilled": True},
            event.acknowledged_at,
        )
        # Retain the original user attribution for acknowledgements made before batch 7.
        row.actor_type = "user"
        row.actor_id = event.acknowledged_by
    incident.started_at = min(incident.started_at, event.starts_at)
    incident.last_alert_started_at = max(incident.last_alert_started_at, event.starts_at)
    if event.severity == "critical":
        incident.severity = "critical"
    _repository.attach_alert_query_5(db, event)
    from app.services.operations.impact import add_candidates

    add_candidates(db, incident)
    from app.services.operations.response import initialize

    initialize(db, incident, created=created)
    return incident


def recompute(db, incident, handling=False, actor=None):
    _repository.recompute_query(db)
    events = linked_alerts(db, incident.id)
    incident.started_at = min(event.starts_at for event in events)
    incident.last_alert_started_at = max(event.starts_at for event in events)
    if any(event.severity == "critical" for event in events):
        incident.severity = "critical"
    if any(event.status == "firing" for event in events):
        incident.recovered_at = None
        if handling:
            next_status = "in_progress" if incident.was_in_progress else "open"
            if next_status != incident.status:
                append_timeline(
                    db,
                    incident,
                    "status_changed",
                    {"from": incident.status, "to": next_status, "reason": "incident_merge"},
                    actor=actor,
                )
                incident.status = next_status
    elif all(
        event.recovery_status == "verified" and event.recovery_evidence_at for event in events
    ):
        recovered_at = max(event.recovery_evidence_at for event in events)
        if incident.status != "recovered":
            append_timeline(
                db,
                incident,
                "status_changed",
                {"from": incident.status, "to": "recovered"},
                recovered_at,
                actor=actor,
            )
        incident.status = "recovered"
        incident.recovered_at = recovered_at
    else:
        incident.recovered_at = None
        if incident.status == "recovered":
            next_status = "in_progress" if incident.was_in_progress else "open"
            append_timeline(
                db,
                incident,
                "status_changed",
                {"from": "recovered", "to": next_status, "reason": "recovery_unverified"},
                actor=actor,
            )
            incident.status = next_status
