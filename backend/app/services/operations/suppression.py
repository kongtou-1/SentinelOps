from datetime import UTC, datetime

from fastapi import HTTPException

from app.models import AlertInhibition, AlertRule, Application, MaintenanceWindow, Server
from app.repositories import notifications as repository
from app.repositories import operations_impact
from app.services.incidents.logic import lock_incidents
from app.services.pagination import page
from app.services.records import active_record
from app.utils.redaction import redact


def event_suppression(db, event, now=None):
    now = now or datetime.now(UTC)
    services = operations_impact.event_applications(db, event)
    values = {"service": services, "server": {event.server_id}, "rule": {event.rule_id}}
    reasons = []
    for window in repository.active_windows(db, now):
        if window.scope_id in values.get(window.scope_kind, set()):
            reasons.append(
                {
                    "kind": window.kind,
                    "id": str(window.id),
                    "reason": window.reason,
                    "ends_at": window.ends_at.isoformat(),
                }
            )
    for item in repository.inhibitions(db):
        if item.child_rule_id != event.rule_id:
            continue
        rule = db.get(AlertRule, item.parent_rule_id)
        if not rule or rule.deleted_at or not rule.enabled:
            continue
        for parent in repository.firing_parent_events(db, item.parent_rule_id):
            same_scope = bool(
                (event.server_id and event.server_id == parent.server_id)
                or (event.llm_endpoint_id and event.llm_endpoint_id == parent.llm_endpoint_id)
                or (services & operations_impact.event_applications(db, parent))
            )
            if same_scope:
                reasons.append(
                    {
                        "kind": "inhibition",
                        "id": str(item.id),
                        "parent_alert_id": str(parent.id),
                        "reason": "explicit_parent_rule",
                    }
                )
                break
    return reasons


def incident_suppression(db, incident_id, now=None):
    events = [e for e in operations_impact.incident_events(db, incident_id) if e.status == "firing"]
    values = [event_suppression(db, event, now) for event in events]
    return [reason for value in values for reason in value] if values and all(values) else []


def listing(db, response, offset=0, limit=50):
    return page(
        db,
        repository.windows_query().order_by(MaintenanceWindow.starts_at.desc()),
        response,
        offset,
        limit,
    )


def inhibition_listing(db, response, offset=0, limit=50):
    return page(db, repository.inhibition_query(), response, offset, limit)


def configure_window(db, body, user, identifier=None):
    lock_incidents(db)
    row = active_record(db, MaintenanceWindow, identifier) if identifier else None
    if body.expected_version != (row.version if row else 0):
        raise HTTPException(409, "Maintenance version changed")
    model = {"service": Application, "server": Server, "rule": AlertRule}[body.scope_kind]
    active_record(db, model, body.scope_id)
    if body.ends_at <= datetime.now(UTC):
        raise HTTPException(422, "Maintenance must end in the future")
    if not row:
        row = MaintenanceWindow(created_by=user.id, version=0)
        db.add(row)
    for key, value in redact(body.model_dump(exclude={"expected_version"})).items():
        setattr(row, key, value)
    row.version += 1
    db.flush()
    repository.audit(db, user, "maintenance_saved", "maintenance", row.id, {"version": row.version})
    db.commit()
    db.refresh(row)
    return row


def remove_window(db, identifier, version, user):
    lock_incidents(db)
    row = active_record(db, MaintenanceWindow, identifier)
    if row.version != version:
        raise HTTPException(409, "Maintenance version changed")
    row.deleted_at = datetime.now(UTC)
    repository.audit(db, user, "maintenance_deleted", "maintenance", row.id)
    db.commit()


def create_inhibition(db, body, user):
    lock_incidents(db)
    active_record(db, AlertRule, body.parent_rule_id)
    active_record(db, AlertRule, body.child_rule_id)
    edges = {}
    for item in repository.inhibitions(db):
        if (item.parent_rule_id, item.child_rule_id) == (body.parent_rule_id, body.child_rule_id):
            return item
        edges.setdefault(item.parent_rule_id, set()).add(item.child_rule_id)
    pending, visited = [body.child_rule_id], set()
    while pending:
        identifier = pending.pop()
        if identifier == body.parent_rule_id:
            raise HTTPException(422, "Inhibition must not create a cycle")
        if identifier not in visited:
            visited.add(identifier)
            pending.extend(edges.get(identifier, ()))
    row = repository.existing_inhibition(db, body.parent_rule_id, body.child_rule_id)
    if row:
        row.deleted_at = None
    else:
        row = AlertInhibition(**body.model_dump(), created_by=user.id)
        db.add(row)
    db.flush()
    repository.audit(db, user, "inhibition_created", "inhibition", row.id)
    db.commit()
    return row


def remove_inhibition(db, identifier, user):
    lock_incidents(db)
    row = active_record(db, AlertInhibition, identifier)
    row.deleted_at = datetime.now(UTC)
    repository.audit(db, user, "inhibition_deleted", "inhibition", row.id)
    db.commit()
