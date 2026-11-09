"""Private diagnosis sessions; only adopted snapshots enter the incident workflow."""

from datetime import UTC, datetime

from fastapi import HTTPException

import app.repositories.diagnosis.conversations as _repository
from app.models import (
    AgentAnalysis,
    AnalysisConversation,
    AnalysisEvent,
    AnalysisMessage,
    AnalysisRun,
    Incident,
)
from app.services.access_control import has_permission
from app.services.diagnosis.analyses import (
    available,
    configuration,
    fingerprint,
    requester_valid,
    scope_ids,
    validate_result,
)
from app.services.incidents.logic import append_timeline, lock_incidents, writable
from app.services.records import active_record
from app.utils.redaction import redact

ACTIVE = ("queued", "running", "cancelling")


def access(db, identifier, user, write=False, lock=False):
    if not has_permission(user, "analyses:run", db):
        raise HTTPException(403, "Private conversations require diagnosis permission")
    row = active_record(db, AnalysisConversation, identifier)
    from app.services.access_control import record_allowed

    if not record_allowed(db, user, AnalysisConversation, identifier, "analyses:run"):
        raise HTTPException(403, "Conversation access is outside the authorized scope")
    active_record(db, Incident, row.incident_id)
    if row.owner_id != user.id and (
        write or not has_permission(user, "analysis_conversations:read_all", db)
    ):
        raise HTTPException(403, "This conversation belongs to another user")
    if lock:
        row = _repository.access_query(db, identifier)
        if row is None or row.deleted_at is not None:
            raise HTTPException(404, "Record not found")
    return row


def remove(db, identifier, user):
    conversation = access(db, identifier, user, write=True, lock=True)
    if _repository.send_query_2(ACTIVE, db, identifier) is not None:
        raise HTTPException(409, "请先停止本轮诊断，等待运行结束后删除会话")
    conversation.deleted_at = conversation.updated_at = datetime.now(UTC)
    db.commit()


def snapshot(db, row):
    owner, run = _repository.snapshot_query(db, row)
    return {
        **{
            key: getattr(row, key)
            for key in (
                "id",
                "incident_id",
                "owner_id",
                "title",
                "last_seq",
                "created_at",
                "updated_at",
            )
        },
        "owner_name": owner.username,
        "current_run": run,
    }


def event(db, conversation, run, kind, data):
    conversation.last_seq += 1
    item = AnalysisEvent(
        conversation_id=conversation.id,
        run_id=run.id if run else None,
        seq=conversation.last_seq,
        kind=kind,
        data=redact(data),
    )
    _repository.event_query(db, item)
    return item


def send(db, identifier, user, body):
    lock_incidents(db)
    conversation = access(db, identifier, user, write=True, lock=True)
    previous = _repository.send_query(body, db, identifier)
    if previous:
        return previous
    available()
    writable(active_record(db, Incident, conversation.incident_id))
    content = redact(body.content.strip())
    if not content:
        raise HTTPException(422, "Message cannot be blank")
    run = _repository.send_query_2(ACTIVE, db, identifier)
    if run is None:
        from app.services.database_diagnosis import freeze_database_context
        from app.services.sources.diagnostics import freeze_source_context

        run = AnalysisRun(
            conversation_id=identifier,
            incident_id=conversation.incident_id,
            requested_by=user.id,
            config=configuration(),
            source_context=freeze_source_context(db, conversation.incident_id),
            database_context=freeze_database_context(db, conversation.incident_id),
        )
        _repository.send_query_3(db, run)
        event(db, conversation, run, "run_queued", {})
        # Unconsumed supplements require an explicit new message to resume them.
        for pending in _repository.send_query_4(db, identifier):
            pending.run_id, pending.status, pending.sdk_message_id = run.id, "saved", None
    message = AnalysisMessage(
        conversation_id=identifier,
        run_id=run.id,
        role="user",
        content=content,
        status="next_round" if run.status == "cancelling" else "saved",
        client_message_id=body.client_message_id,
    )
    _repository.send_query_5(db, message)
    event(
        db,
        conversation,
        run,
        "message_saved",
        {"message_id": str(message.id), "status": message.status},
    )
    db.commit()
    _repository.send_query_6(db, message)
    return message


def run_access(db, identifier, user, write=False):
    run = active_record(db, AnalysisRun, identifier)
    access(db, run.conversation_id, user, write=write)
    return run


def cancel(db, identifier, user):
    run = run_access(db, identifier, user, write=True)
    conversation = access(db, run.conversation_id, user, write=True, lock=True)
    _repository.cancel_query(db, run)
    if run.status not in ("queued", "running"):
        return run
    run.status = "cancelled" if run.status == "queued" else "cancelling"
    if run.status == "cancelled":
        run.finished_at = datetime.now(UTC)
        leave_pending(db, run)
    event(db, conversation, run, "run_" + run.status, {})
    db.commit()
    _repository.cancel_query_2(db, run)
    return run


def leave_pending(db, run):
    for message in _repository.leave_pending_query(db, run):
        message.status = "unknown" if message.status == "dispatched" else "next_round"
    for message in _repository.leave_pending_query_2(db, run):
        message.status = "partial"


def valid_knowledge(db, knowledge):
    result = []
    for item in knowledge:
        try:
            chunk, document = _repository.valid_knowledge_query(db, item)
            if (
                chunk
                and not chunk.deleted_at
                and document
                and not document.deleted_at
                and document.enabled
                and document.index_status == "ready"
                and chunk.version == document.version == item["version"]
            ):
                result.append(item)
        except (ValueError, KeyError, TypeError):
            continue
    return result


def valid_evidence(db, run, evidence):
    from app.services.database_diagnosis import database_evidence_valid
    from app.services.sources.diagnostics import source_evidence_valid

    allowed = {str(identifier) for identifier in scope_ids(db, run.incident_id)}
    return [
        item
        for item in evidence
        if item.get("source")
        not in (
            "submit_diagnosis",
            "propose_action_skill",
            "list_skills",
            "read_skill",
            "search_agent_history",
            "read_agent_history",
        )
        and (not item.get("asset_id") or item["asset_id"] in allowed)
        and source_evidence_valid(db, run.source_context, item)
        and database_evidence_valid(db, run.database_context, item, run_started_at=run.started_at)
    ]


def adopt(db, identifier, user):
    lock_incidents(db)
    run = run_access(db, identifier, user, write=True)
    conversation = access(db, run.conversation_id, user, write=True, lock=True)
    _repository.adopt_query(db, run)
    if run.adopted_analysis_id:
        return active_record(db, AgentAnalysis, run.adopted_analysis_id)
    incident = active_record(db, Incident, run.incident_id)
    writable(incident)
    if run.status != "completed" or not run.candidate or not requester_valid(db, run):
        raise HTTPException(409, "Only a completed valid diagnosis can be adopted")
    try:
        result = validate_result(
            db,
            run,
            run.candidate,
            valid_evidence(db, run, run.evidence),
            valid_knowledge(db, run.knowledge),
        )
    except (ValueError, TypeError):
        raise HTTPException(409, "Diagnosis evidence or proposal is no longer valid") from None
    # Do not publish conversation text, raw SDK prompts or other private messages.
    references = set(result["evidence_ids"])
    for cause in result["possible_causes"]:
        references.update(cause["evidence_ids"])
    published = [
        e
        for e in run.evidence
        if e["id"] in references
        or e.get("source") in ("get_alert_context", "list_skills", "read_skill")
    ]
    published_ids = {e["id"] for e in published}
    calls = [
        call
        for source in _repository.adoption_runs(db, run)
        for call in source.tool_calls
        if call.get("evidence_id") in published_ids
        and call.get("tool") not in ("submit_diagnosis", "propose_action_skill")
    ]
    analysis = AgentAnalysis(
        incident_id=run.incident_id,
        requested_by=user.id,
        status="completed",
        input_hash=fingerprint(db, incident),
        config=run.config,
        source_context=run.source_context,
        database_context=run.database_context,
        session_id=run.session_id,
        started_at=run.started_at,
        finished_at=run.finished_at,
        result=result,
        evidence=redact(published),
        knowledge=[k for k in run.knowledge if k["id"] in result["knowledge_ids"]],
        tool_calls=redact(list({c["evidence_id"]: c for c in calls}.values())),
    )
    _repository.adopt_query_2(analysis, db)
    run.adopted_analysis_id = analysis.id
    event(db, conversation, run, "diagnosis_adopted", {"analysis_id": str(analysis.id)})
    append_timeline(
        db,
        incident,
        "analysis_completed",
        {"analysis_id": str(analysis.id), "source": "adopted_conversation"},
        actor=user,
    )
    db.commit()
    _repository.adopt_query_3(analysis, db)
    return analysis
