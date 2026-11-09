"""Authorization transitions are committed before any remote mutation."""

from datetime import UTC, datetime, timedelta

from fastapi import HTTPException

import app.repositories.executions.actions as _repository
from app.models import (
    AgentAnalysis,
    Approval,
    Container,
    Incident,
    SkillExecution,
    SSHCredential,
)
from app.services.diagnosis.analyses import validate_proposal
from app.services.executions.skills import RESTART, digest, docker_identifier
from app.services.incidents.logic import append_timeline, lock_incidents
from app.services.records import active_record
from app.utils.redaction import redact

ACTIVE = ("queued", "prechecking", "executing", "verifying")


def audit(db, approval, action, user=None, execution=None, detail=None, request=None):
    content = {"approval_id": str(approval.id), **(detail or {})}
    if execution:
        content["execution_id"] = str(execution.id)
    content = redact(content)
    incident = _repository.audit_query(
        action,
        approval,
        content,
        db,
        execution,
        request.client.host[:100] if request and request.client else None,
        user,
    )
    append_timeline(db, incident, action, content, actor=user)


def user_valid(db, identifier, admin=False, permission="executions:run"):
    from app.repositories.access import active_user
    from app.services.access_control import Authorization

    user = active_user(db, identifier)
    return bool(
        user and (user.role.code == "admin" if admin else Authorization(db, user).has(permission))
    )


def binding_for(db, incident_id, analysis):
    if analysis.status != "completed" or analysis.incident_id != incident_id:
        raise ValueError("analysis_unavailable")
    proposal = validate_proposal(db, incident_id, analysis.result.get("proposal"))
    container = active_record(db, Container, proposal["parameters"]["container_id"])
    from app.services.access_control import container_server

    server = container_server(db, container)
    if not server.credential_id or not server.ssh_host_key:
        raise ValueError("ssh_unconfigured")
    credential = active_record(db, SSHCredential, server.credential_id)
    return redact(
        {
            "proposal": proposal,
            "skill_digest": digest(RESTART),
            "server_id": str(server.id),
            "container_id": str(container.id),
            "docker_id": docker_identifier(container.docker_id),
            "connection": {
                "host": server.host,
                "port": server.ssh_port,
                "user": server.ssh_user,
                "host_key": server.ssh_host_key,
            },
            "credential_id": str(credential.id),
            "credential_version": credential.updated_at.isoformat(),
        }
    )


def invalid_reason(db, approval, execution=None):
    if approval.expires_at <= datetime.now(UTC):
        return "approval_expired"
    incident = _repository.invalid_reason_query(approval, db)
    if incident.deleted_at or incident.status not in ("open", "in_progress"):
        return "incident_changed"
    if not user_valid(db, approval.requested_by):
        return "requester_unavailable"
    if approval.approver_id and not user_valid(db, approval.approver_id, admin=True):
        return "approver_unavailable"
    if execution and not user_valid(db, execution.initiated_by):
        return "initiator_unavailable"
    from app.repositories.access import active_user
    from app.services.access_control import record_allowed

    for identifier in {
        approval.requested_by,
        execution.initiated_by if execution else approval.requested_by,
    }:
        actor = active_user(db, identifier)
        if (
            not actor
            or not record_allowed(db, actor, Container, approval.container_id, "executions:run")
            or not record_allowed(db, actor, Incident, approval.incident_id, "executions:run")
        ):
            return "requester_unavailable"
    try:
        analysis = active_record(db, AgentAnalysis, approval.analysis_id)
        current = binding_for(db, approval.incident_id, analysis)
    except (ValueError, TypeError, HTTPException):
        return "target_unavailable"
    if (
        digest(approval.binding) != approval.binding_hash
        or digest(current) != approval.binding_hash
        or str(approval.container_id) != current["container_id"]
        or (
            execution
            and (
                execution.binding_hash != approval.binding_hash
                or execution.container_id != approval.container_id
                or execution.incident_id != approval.incident_id
            )
        )
    ):
        return "binding_changed"
    return None


def invalidate(db, approval, reason, user=None, request=None):
    approval.status = "expired" if reason == "approval_expired" else "invalidated"
    approval.error_code = reason
    audit(
        db, approval, "approval_invalidated", user, detail={"error_code": reason}, request=request
    )


def require_valid(db, approval, user=None, request=None):
    reason = invalid_reason(db, approval)
    if reason:
        invalidate(db, approval, reason, user, request)
        db.commit()
        raise HTTPException(409, reason)


def create_approval(db, incident_id, analysis_id, user, request):
    lock_incidents(db)
    incident = active_record(db, Incident, incident_id)
    if incident.status not in ("open", "in_progress"):
        raise HTTPException(409, "Incident is not active")
    analysis = active_record(db, AgentAnalysis, analysis_id)
    try:
        binding = binding_for(db, incident_id, analysis)
    except (ValueError, TypeError):
        raise HTTPException(
            409, "Completed, valid proposal and configured SSH are required"
        ) from None
    previous = _repository.create_approval_query(analysis_id, db)
    if previous:
        reason = invalid_reason(db, previous)
        if not reason:
            return previous
        invalidate(db, previous, reason, user, request)
        _repository.create_approval_query_2(db)
    row = Approval(
        incident_id=incident_id,
        analysis_id=analysis_id,
        container_id=binding["container_id"],
        requested_by=user.id,
        binding=redact(binding),
        binding_hash=digest(binding),
        expires_at=datetime.now(UTC) + timedelta(minutes=30),
    )
    _repository.create_approval_query_3(db, row)
    audit(db, row, "approval_requested", user, request=request)
    db.commit()
    _repository.create_approval_query_4(db, row)
    return row


def locked_approval(db, identifier):
    lock_incidents(db)
    return _repository.locked_approval_query(db, identifier) or active_record(
        db, Approval, identifier
    )


def decide(db, identifier, user, approved, comment, request):
    row = locked_approval(db, identifier)
    if row.status != "pending":
        audit(db, row, "approval_denied", user, detail={"reason": "invalid_state"}, request=request)
        db.commit()
        raise HTTPException(409, "Approval is not pending")
    require_valid(db, row, user, request)
    row.status = "approved" if approved else "rejected"
    row.approver_id, row.decided_at = user.id, datetime.now(UTC)
    row.decision_comment = redact(comment)
    audit(db, row, "approval_approved" if approved else "approval_rejected", user, request=request)
    db.commit()
    _repository.decide_query(db, row)
    return row


def enqueue(db, identifier, user, request):
    row = locked_approval(db, identifier)
    existing = _repository.enqueue_query(db, row)
    if existing:
        return existing
    if row.status != "approved" or not row.approver_id:
        audit(db, row, "execution_denied", user, detail={"reason": "not_approved"}, request=request)
        db.commit()
        raise HTTPException(409, "Approval is not approved")
    require_valid(db, row, user, request)
    if _repository.enqueue_query_2(ACTIVE, db, row):
        audit(
            db, row, "execution_denied", user, detail={"reason": "container_busy"}, request=request
        )
        db.commit()
        raise HTTPException(409, "Container already has an active execution")
    execution = SkillExecution(
        approval_id=row.id,
        incident_id=row.incident_id,
        container_id=row.container_id,
        initiated_by=user.id,
        binding_hash=row.binding_hash,
    )
    row.status = "consumed"
    _repository.enqueue_query_3(db, execution)
    audit(db, row, "execution_queued", user, execution=execution, request=request)
    db.commit()
    _repository.enqueue_query_4(db, execution)
    return execution
