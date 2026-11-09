from datetime import UTC, datetime

from fastapi import HTTPException

import app.repositories.incidents.management as _repository
from app.core.security import ROLE_PERMISSIONS
from app.models import (
    Incident,
    IncidentTimeline,
)
from app.schemas.incidents import (
    IncidentResponse,
)
from app.services.incidents.logic import (
    append_timeline,
    linked_alerts,
    lock_incidents,
    recompute,
    writable,
)
from app.services.pagination import page
from app.services.records import active_record
from app.utils.redaction import redact


def locked_record(db, incident_id):
    lock_incidents(db)
    incident = active_record(db, Incident, incident_id)
    _repository.locked_record_query(db, incident)
    return incident


def incidents(
    response,
    db,
    user,
    status=None,
    severity=None,
    assignee_id=None,
    server_id=None,
    llm_endpoint_id=None,
    start=None,
    end=None,
    unassigned=False,
    offset=0,
    limit=50,
):
    if start and end and (end < start):
        raise HTTPException(422, "End must not precede start")
    query = _repository.incidents_query()
    for field, value in (("status", status), ("severity", severity), ("assignee_id", assignee_id)):
        if value is not None:
            query = _repository.incidents_query_2(field, query, value)
    if start:
        query = _repository.incidents_query_3(query, start)
    if end:
        query = _repository.incidents_query_4(end, query)
    for field, value in (("server_id", server_id), ("llm_endpoint_id", llm_endpoint_id)):
        if value is None:
            continue
        members, query = _repository.incidents_query_5(field, query, value)
    if unassigned:
        query = _repository.incidents_query_6(query)
    return page(db, _repository.incidents_query_7(query), response, offset, limit)


def assignees(response, db, user, offset=0, limit=50):
    return [
        {"id": item.id, "username": item.username, "role": item.role.code}
        for item in page(
            db,
            _repository.assignees_query(),
            response,
            offset,
            limit,
        )
    ]


def detail(incident_id, db, user):
    from app.repositories.monitoring.service_monitors import incident_monitors

    incident = active_record(db, Incident, incident_id)
    events = linked_alerts(db, incident_id)
    timeline = _repository.detail_query(db, incident_id)
    return {
        **IncidentResponse.model_validate(incident).model_dump(),
        "service_monitors": incident_monitors(db, incident_id),
        "server_ids": sorted({item.server_id for item in events if item.server_id}),
        "llm_endpoint_ids": sorted(
            {item.llm_endpoint_id for item in events if item.llm_endpoint_id}
        ),
        "alert_count": len(events),
        "firing_alert_count": sum(item.status == "firing" for item in events),
        "recovery_pending_alert_count": sum(
            item.status == "resolved" and item.recovery_status == "pending" for item in events
        ),
        "recovery_terminated_alert_count": sum(
            item.recovery_status == "terminated" for item in events
        ),
        "recovery_verified_alert_count": sum(item.recovery_status == "verified" for item in events),
        "timeline": timeline,
    }


def alerts(response, incident_id, db, user, offset=0, limit=50):
    active_record(db, Incident, incident_id)
    return page(
        db,
        _repository.alerts_query(incident_id),
        response,
        offset,
        limit,
    )


def timeline(response, incident_id, db, user, offset=0, limit=50):
    active_record(db, Incident, incident_id)
    return page(
        db,
        _repository.timeline_query(incident_id),
        response,
        offset,
        limit,
    )


def update(incident_id, body, db, user):
    incident = locked_record(db, incident_id)
    writable(incident)
    if body.assignee_id is not None:
        assignee = _repository.update_query(body, db)
        from app.services.access_control import record_allowed

        if assignee is None or not record_allowed(
            db, assignee, Incident, incident_id, "incidents:handle"
        ):
            raise HTTPException(422, "Assignee must have incident handling access")
    for field, value in body.model_dump(exclude_unset=True).items():
        before = getattr(incident, field)
        if before != value:
            setattr(incident, field, value)
            append_timeline(
                db,
                incident,
                "assignee_changed" if field == "assignee_id" else "title_changed",
                {
                    "from": str(before) if before is not None else None,
                    "to": str(value) if value is not None else None,
                },
                actor=user,
            )
    db.commit()
    _repository.update_query_2(db, incident)
    return incident


def status(incident_id, body, request, db, user):
    if body.force and "incidents:force_close" not in ROLE_PERMISSIONS.get(user.role.code, ()):
        raise HTTPException(403, "Only Admin can force-close an incident")
    incident = locked_record(db, incident_id)
    if incident.status == body.status:
        return incident
    writable(incident)
    events = linked_alerts(db, incident.id)
    terminated = (
        bool(events)
        and all(
            item.status == "resolved" and item.recovery_status in ("verified", "terminated")
            for item in events
        )
        and any(item.recovery_status == "terminated" for item in events)
    )
    valid = (
        incident.status in ("open", "in_progress") and body.status in ("open", "in_progress")
    ) or (body.status == "closed" and (body.force or incident.status == "recovered" or terminated))
    if not valid or (
        body.status == "closed"
        and not body.force
        and any(item.status == "firing" for item in events)
    ):
        raise HTTPException(409, "Invalid incident state transition")
    previous = incident.status
    incident.status = body.status
    if body.status == "in_progress":
        incident.was_in_progress = True
        from app.services.operations.response import accept

        accept(db, incident, user)
    if body.status == "closed":
        incident.closed_at = datetime.now(UTC)
        incident.closure = {
            "forced": body.force,
            "reason": redact(body.comment),
            "actor_id": str(user.id),
            "actor_username": user.username,
            "closed_at": incident.closed_at.isoformat(),
            "recovery_verified": bool(events)
            and all(
                item.status == "resolved"
                and item.recovery_status == "verified"
                and item.recovery_evidence_at
                for item in events
            ),
            "alerts": [
                {
                    "alert_id": str(item.id),
                    "alertname": item.labels.get("alertname"),
                    "status": item.status,
                    "recovery_status": item.recovery_status,
                    "recovery_reason": item.recovery_reason,
                    "recovery_evidence_at": item.recovery_evidence_at.isoformat()
                    if item.recovery_evidence_at
                    else None,
                }
                for item in events
            ],
        }
        from app.services.executions.actions import invalidate

        for approval in _repository.status_query(db, incident):
            invalidate(db, approval, "incident_changed", user, request)
        action = "incident_force_closed" if body.force else "incident_closed"
        _repository.status_query_2(
            action, db, incident, request.client.host[:100] if request.client else None, user
        )
        if body.force:
            append_timeline(db, incident, action, incident.closure, actor=user)
    append_timeline(
        db,
        incident,
        "status_changed",
        {
            "from": previous,
            "to": body.status,
            "comment": redact(body.comment),
            "forced": body.force,
        },
        actor=user,
    )
    db.commit()
    _repository.status_query_3(db, incident)
    return incident


def comment(incident_id, body, db, user):
    incident = locked_record(db, incident_id)
    writable(incident)
    previous = _repository.comment_query(db, incident)
    if (
        previous
        and previous.event_type == "comment_added"
        and previous.actor_id == user.id
        and previous.content == {"comment": body.comment}
    ):
        return previous
    row = IncidentTimeline(
        incident_id=incident.id,
        event_type="comment_added",
        occurred_at=datetime.now(UTC),
        actor_type="user",
        actor_id=user.id,
        content={"comment": body.comment},
    )
    _repository.comment_query_2(db, row)
    db.commit()
    _repository.comment_query_3(db, row)
    return row


def merge(incident_id, body, db, user):
    if incident_id == body.target_incident_id:
        raise HTTPException(422, "Cannot merge an incident into itself")
    source = locked_record(db, incident_id)
    target = active_record(db, Incident, body.target_incident_id)
    _repository.merge_query(db, target)
    if source.status == "merged" and source.merged_into_id == target.id:
        return target
    writable(source)
    writable(target)
    before = {
        "source_status": source.status,
        "target_status": target.status,
        "source_assignee_id": str(source.assignee_id) if source.assignee_id else None,
        "target_assignee_id": str(target.assignee_id) if target.assignee_id else None,
    }
    members = _repository.merge_query_2(db, source)
    for member in members:
        member.incident_id = target.id
    target.was_in_progress = target.was_in_progress or source.was_in_progress
    if source.severity == "critical":
        target.severity = "critical"
    source.status = "merged"
    source.merged_into_id = target.id
    content = {
        **before,
        "source_incident_id": str(source.id),
        "target_incident_id": str(target.id),
        "comment": body.comment,
        "moved_alert_count": len(members),
    }
    append_timeline(db, source, "incident_merged", content, actor=user)
    append_timeline(db, target, "incident_merged", content, actor=user)
    recompute(db, target, handling=True, actor=user)
    db.commit()
    _repository.merge_query_3(db, target)
    return target
