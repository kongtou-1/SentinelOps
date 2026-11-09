from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi import HTTPException

from app.models import Incident, IncidentResponseRecord
from app.repositories import notifications as repository
from app.repositories import operations_impact
from app.services.incidents.logic import append_timeline, lock_incidents, writable
from app.services.operations.impact import service_ids
from app.services.operations.notifications import enqueue
from app.services.operations.suppression import incident_suppression
from app.services.records import active_record


def initialize(db, incident, created=False):
    row = repository.response(db, incident.id)
    if row is None and not created:
        return None
    if row is None:
        row = IncidentResponseRecord(
            incident_id=incident.id,
            snapshot={"services": {}, "initial_notified": False},
            due_at=incident.created_at
            + timedelta(seconds=300 if incident.severity == "critical" else 900),
        )
        db.add(row)
    snapshot = dict(row.snapshot)
    services = dict(snapshot.get("services", {}))
    for identifier in service_ids(db, incident.id):
        if str(identifier) not in services:
            policy = operations_impact.policy(db, identifier)
            config = policy.response_config if policy else {}
            services[str(identifier)] = {"version": policy.version if policy else 0, **config}
            seconds = (
                config.get("critical_seconds", 300)
                if incident.severity == "critical"
                else config.get("warning_seconds", 900)
            )
            row.due_at = min(row.due_at, incident.created_at + timedelta(seconds=seconds))
    snapshot["services"] = services
    # Severity may rise; frozen configuration must never extend an existing deadline.
    for config in services.values():
        seconds = (
            config.get("critical_seconds", 300)
            if incident.severity == "critical"
            else config.get("warning_seconds", 900)
        )
        row.due_at = min(row.due_at, incident.created_at + timedelta(seconds=seconds))
    row.snapshot = snapshot
    db.flush()
    notify_initial(db, incident, row)
    return row


def recipients(db, row, backup=False):
    primary, watchers, backups = set(), set(), set()
    for config in row.snapshot.get("services", {}).values():
        for field, target in (("primary_id", primary), ("backup_id", backups)):
            if config.get(field):
                user = repository.active_user(db, UUID(config[field]), handler=True)
                if user:
                    target.add(user.id)
        watchers.update(UUID(i) for i in config.get("notify_user_ids", []))
    admins = {user.id for user in repository.admins(db)}
    return (backups | admins) if backup else ((primary or admins) | watchers)


def notify_initial(db, incident, row):
    if incident_suppression(db, incident.id):
        return
    users = recipients(db, row)
    if users:
        enqueue(
            db,
            users,
            "incident_opened",
            "incident",
            incident.id,
            incident.title,
            {"due_at": row.due_at.isoformat()},
            incident_id=incident.id,
        )
        row.snapshot = {**row.snapshot, "initial_notified": True}


def accept(db, incident, user):
    row = repository.response(db, incident.id)
    if row and row.accepted_at is None:
        row.accepted_at, row.accepted_by = datetime.now(UTC), user.id
        append_timeline(
            db, incident, "incident_accepted", {"accepted_by": str(user.id)}, actor=user
        )
    return row


def accept_incident(db, incident_id, user):
    lock_incidents(db)
    incident = active_record(db, Incident, incident_id)
    writable(incident)
    if incident.status not in ("open", "in_progress"):
        raise HTTPException(409, "Only active incidents can be accepted")
    row = repository.response(db, incident_id)
    if not row:
        # Explicit legacy acceptance does not backfill notifications.
        row = IncidentResponseRecord(
            incident_id=incident.id,
            snapshot={"services": {}, "initial_notified": True, "legacy_manual": True},
            due_at=datetime.now(UTC),
        )
        db.add(row)
        db.flush()
    accept(db, incident, user)
    if incident.status == "open":
        append_timeline(
            db, incident, "status_changed", {"from": "open", "to": "in_progress"}, actor=user
        )
        incident.status, incident.was_in_progress = "in_progress", True
    db.commit()
    return detail(db, incident_id)


def detail(db, incident_id):
    active_record(db, Incident, incident_id)
    row = repository.response(db, incident_id)
    return {
        "enabled": row is not None,
        "snapshot": row.snapshot if row else {},
        "due_at": row.due_at if row else None,
        "accepted_at": row.accepted_at if row else None,
        "accepted_by": row.accepted_by if row else None,
        "escalated_at": row.escalated_at if row else None,
        "overdue": bool(row and not row.accepted_at and row.due_at < datetime.now(UTC)),
        "suppression": incident_suppression(db, incident_id),
    }


def reconcile(db, now=None):
    now = now or datetime.now(UTC)
    identifiers = repository.due_responses(db, now)
    db.rollback()
    for identifier in identifiers:
        lock_incidents(db)
        incident = db.get(Incident, identifier)
        row = repository.response(db, identifier)
        if (
            not row
            or incident.status not in ("open", "in_progress")
            or incident_suppression(db, identifier, now)
        ):
            db.rollback()
            continue
        deferred = not row.snapshot.get("initial_notified", False)
        notify_initial(db, incident, row)
        if deferred:
            from app.services.diagnosis.policy import enqueue_automatic
            from app.services.incidents.logic import linked_alerts

            enqueue_automatic(db, incident, linked_alerts(db, identifier))
        if row.accepted_at is None and row.escalated_at is None and row.due_at <= now:
            enqueue(
                db,
                recipients(db, row, backup=True),
                "incident_overdue",
                "incident",
                identifier,
                "接单超时：" + incident.title,
                {"due_at": row.due_at.isoformat()},
                incident_id=identifier,
            )
            row.escalated_at = now
            append_timeline(db, incident, "response_escalated", {"due_at": row.due_at.isoformat()})
        db.commit()
