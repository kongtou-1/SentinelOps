"""Admin-configured, one-time automatic diagnosis through the existing queue."""

import logging

from fastapi import HTTPException

import app.repositories.diagnosis.policy as repository
from app.models import DiagnosisPolicy
from app.services.diagnosis.analyses import available, enqueue
from app.services.incidents.logic import append_timeline, linked_alerts, lock_incidents

logger = logging.getLogger(__name__)


def read(db, user):
    row = repository.policy(db)
    try:
        available()
        ready = True
    except HTTPException:
        ready = False
    return {
        "enabled": row.enabled if row else False,
        "configured_by": row.configured_by if row else None,
        "updated_at": row.updated_at if row else None,
        "ai_available": ready,
    }


def update(db, body, user):
    lock_incidents(db)
    row = repository.policy(db)
    if row is None:
        row = DiagnosisPolicy(id=1)
        repository.add(db, row)
    row.enabled, row.configured_by = body.enabled, user.id
    db.commit()
    return read(db, user)


def author(db, events):
    default = repository.policy(db)
    for event in sorted(events, key=lambda item: str(item.id)):
        from app.services.operations.suppression import event_suppression

        if event_suppression(db, event):
            continue
        if event.labels.get("rule_id") and not event.rule_id:
            continue
        rule = repository.rule(db, event.rule_id) if event.rule_id else None
        if event.rule_id and rule is None:
            continue
        if rule and not rule.enabled:
            continue
        if rule and rule.auto_diagnose is not None:
            enabled, user_id = rule.auto_diagnose, rule.auto_diagnose_by
        else:
            enabled = bool(default and default.enabled)
            user_id = default.configured_by if default else None
        if enabled and user_id:
            user = repository.admin(db, user_id)
            if user:
                return user
    return None


def automatic_authorized(db, row):
    return bool(
        repository.admin(db, row.requested_by) and author(db, linked_alerts(db, row.incident_id))
    )


def enqueue_automatic(db, incident, events):
    if incident.status not in ("open", "in_progress") or repository.already_analyzed(
        db, incident.id
    ):
        return
    user = author(db, events)
    if user is None:
        return
    try:
        # A diagnosis/configuration failure must never roll back received alerts.
        with db.begin_nested():
            enqueue(db, incident.id, user, False, trigger="automatic", commit=False)
    except Exception as exc:
        reason = "ai_unavailable" if isinstance(exc, HTTPException) else "enqueue_failed"
        logger.warning("Automatic diagnosis skipped (%s)", type(exc).__name__)
        append_timeline(db, incident, "analysis_auto_skipped", {"reason": reason})
