from uuid import UUID

import app.repositories.alerts as _repository
from app.core.config import get_settings
from app.core.service_auth import service_authorization
from app.models import (
    AlertEvent,
    AlertRule,
    LLMEndpoint,
    PredictionTask,
    Server,
    ServiceMonitor,
)
from app.services.incidents.logic import (
    append_timeline,
    attach_alert,
    event_incident,
    lock_incidents,
    recompute,
    writable,
)
from app.services.monitoring.config import apply_rules
from app.services.pagination import page, time_range
from app.services.records import active_record, save


def rules(response, db, user, offset=0, limit=50):
    return page(
        db,
        _repository.rules_query(),
        response,
        offset,
        limit,
    )


def create_rule(body, db, user):
    lock_incidents(db)
    if body.service_monitor_id:
        active_record(db, ServiceMonitor, body.service_monitor_id)
    if body.prediction_task_id:
        active_record(db, PredictionTask, body.prediction_task_id)
    if body.server_id:
        active_record(db, Server, body.server_id)
    if body.llm_endpoint_id:
        active_record(db, LLMEndpoint, body.llm_endpoint_id)
    return save(
        db,
        AlertRule(
            **body.model_dump(), auto_diagnose_by=user.id if body.auto_diagnose is True else None
        ),
    )


def replace_rule(rule_id, body, db, user):
    lock_incidents(db)
    if body.service_monitor_id:
        active_record(db, ServiceMonitor, body.service_monitor_id)
    rule = active_record(db, AlertRule, rule_id)
    if body.prediction_task_id:
        active_record(db, PredictionTask, body.prediction_task_id)
    if body.server_id:
        active_record(db, Server, body.server_id)
    if body.llm_endpoint_id:
        active_record(db, LLMEndpoint, body.llm_endpoint_id)
    for key, value in body.model_dump().items():
        setattr(rule, key, value)
    rule.auto_diagnose_by = user.id if body.auto_diagnose is True else None
    return save(db, rule)


def delete_rule(rule_id, db, user):
    lock_incidents(db)
    rule = active_record(db, AlertRule, rule_id)
    rule.deleted_at = _repository.delete_rule_query()
    db.commit()


def synchronize_rules(db, user):
    return apply_rules(db)


def active_reference(db, model, value):
    try:
        record_id = UUID(value)
    except (ValueError, TypeError, AttributeError):
        return None
    return _repository.active_reference_query(db, model, record_id)


def webhook(body, db, authorization=None):
    service_authorization(authorization, get_settings().alertmanager_token_file)
    lock_incidents(db)
    changed = 0
    observations = {}
    for alert in sorted(body.alerts, key=lambda item: (item.fingerprint.lower(), item.startsAt)):
        from app.services.monitoring.recovery import notification_snapshot

        values = {
            "rule_snapshot": notification_snapshot(alert.annotations),
            "fingerprint": alert.fingerprint.lower(),
            "starts_at": alert.startsAt,
            "ends_at": alert.endsAt if alert.status == "resolved" else None,
            "status": alert.status,
            "labels": alert.labels,
            "annotations": alert.annotations,
            "severity": alert.labels.get("severity", "warning")[:20],
            "server_id": active_reference(db, Server, alert.labels.get("server_id")),
            "llm_endpoint_id": active_reference(db, LLMEndpoint, alert.labels.get("endpoint_id")),
            "rule_id": active_reference(db, AlertRule, alert.labels.get("rule_id")),
            "service_monitor_id": active_reference(
                db, ServiceMonitor, alert.labels.get("monitor_id")
            ),
            "prediction_task_id": active_reference(db, PredictionTask, alert.labels.get("task_id")),
        }
        event_id = _repository.webhook_query(db, values)
        if event_id:
            changed += 1
        event = _repository.webhook_query_2(alert, db, values)
        if event is None:
            continue
        observation = observations.setdefault(
            event.id, {"event": event, "attach": False, "recovered": False, "new": False}
        )
        observation["new"] |= bool(event_id)
        observation["attach"] |= event.status == "firing"
        # Recovery is terminal for one occurrence. Late/repeated firing cannot reopen it.
        if event and event.status == "firing" and alert.status == "resolved":
            event.status = "resolved"
            event.ends_at = alert.endsAt
            event.annotations = alert.annotations
            observation["recovered"] = True
            changed += 1
    affected, automatic_events = {}, {}
    for observation in observations.values():
        event = observation["event"]
        incident = attach_alert(db, event) if observation["attach"] else event_incident(db, event)
        if incident:
            affected[incident.id] = incident
            if observation["new"] and event.status == "firing":
                automatic_events.setdefault(incident.id, []).append(event)
            if observation["recovered"]:
                append_timeline(
                    db, incident, "alert_resolved", {"alert_event_id": str(event.id)}, event.ends_at
                )
    # Apply recovery after the entire notification, including newly arriving firing alerts.
    for incident in affected.values():
        if incident.status in ("open", "in_progress"):
            recompute(db, incident)
    from app.services.diagnosis.policy import enqueue_automatic

    for incident_id, events in automatic_events.items():
        enqueue_automatic(db, affected[incident_id], events)
    db.commit()
    return {"accepted": True, "received": len(body.alerts), "changed": changed}


def alerts(
    response,
    db,
    user,
    status=None,
    server_id=None,
    llm_endpoint_id=None,
    prediction_task_id=None,
    incident_id=None,
    severity=None,
    acknowledged=None,
    start=None,
    end=None,
    offset=0,
    limit=50,
):
    query = _repository.alerts_query()
    if status:
        query = _repository.alerts_query_2(query, status)
    if server_id:
        query = _repository.alerts_query_3(query, server_id)
    if llm_endpoint_id:
        query = _repository.alerts_query_4(llm_endpoint_id, query)
    if prediction_task_id:
        query = _repository.alerts_query_5(prediction_task_id, query)
    if incident_id:
        query = _repository.alerts_query_6(incident_id, query)
    query = time_range(query, AlertEvent.starts_at, start, end)
    if severity:
        query = _repository.alerts_query_7(query, severity)
    if acknowledged is not None:
        query = _repository.alerts_query_8(acknowledged, query)
    return page(db, _repository.alerts_query_9(query), response, offset, limit)


def alert_detail(alert_id, db, user):
    return active_record(db, AlertEvent, alert_id)


def acknowledge(alert_id, body, db, user):
    lock_incidents(db)
    event = active_record(db, AlertEvent, alert_id)
    _repository.acknowledge_query(db, event)
    if event.acknowledged_at is None:
        incident = event_incident(db, event)
        if incident:
            writable(incident)
        event.acknowledged_at = _repository.acknowledge_query_2()
        event.acknowledged_by = user.id
        event.comment = body.comment
        _repository.acknowledge_query_3(db)
        if incident:
            append_timeline(
                db,
                incident,
                "alert_acknowledged",
                {"alert_event_id": str(event.id), "comment": body.comment},
                event.acknowledged_at,
                actor=user,
            )
        db.commit()
        _repository.acknowledge_query_4(db, event)
    return event
