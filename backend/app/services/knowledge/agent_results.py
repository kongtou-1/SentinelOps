"""Discover summaries first and load bounded sections only when requested."""

import json

from fastapi import HTTPException

from app.models import (
    AnalysisRun,
    Incident,
    InspectionCheckResult,
    InspectionPlan,
    InspectionRun,
    KnowledgeDocument,
)
from app.repositories.knowledge import agent_results as repository
from app.repositories.notifications import active_user, audit
from app.schemas.agent_results import ResultDetail, ResultPage
from app.services.access_control import bind, require
from app.services.knowledge.history_guide import HISTORY_GUIDE as HISTORY_GUIDE
from app.services.records import active_record
from app.utils.redaction import redact


def authorize(db, user, business):
    # Do not let a knowledge-management permission replace the owning business permission.
    permission = "analyses:read" if business == "diagnosis" else "inspections:read"
    bind(db, user)
    require(db, user, permission)


def listing(
    db,
    user,
    business,
    scope_id,
    query="",
    offset=0,
    limit=10,
    conversation_id=None,
    run_id=None,
    exclude_id=None,
):
    authorize(db, user, business)
    if business == "diagnosis":
        active_record(db, Incident, scope_id)
        if conversation_id:
            from app.services.diagnosis.conversations import access

            conversation = access(db, conversation_id, user)
            if conversation.incident_id != scope_id:
                raise HTTPException(404, "Conversation is outside this incident")
    else:
        active_record(db, InspectionPlan, scope_id)
        if conversation_id:
            raise HTTPException(422, "Conversation is only available for diagnosis")
        if run_id and active_record(db, InspectionRun, run_id).plan_id != scope_id:
            raise HTTPException(404, "Run is outside this plan")
    source = repository.history_query(business, scope_id, conversation_id, run_id, exclude_id)
    rows, total = repository.search(db, source, redact(query), offset, limit)
    return ResultPage.model_validate(
        {
            "items": [redact(dict(row)) for row in rows],
            "total": total,
            "next_offset": offset + len(rows) if offset + len(rows) < total else None,
        }
    ).model_dump(mode="json")


def source_record(db, user, kind, identifier):
    business = "diagnosis" if kind in {"diagnosis", "conversation"} else "inspection"
    authorize(db, user, business)
    row = active_record(db, repository.MODELS[kind], identifier)
    if business == "diagnosis":
        parent = active_record(db, Incident, row.incident_id)
        if kind == "conversation":
            from app.services.diagnosis.conversations import access

            access(db, row.conversation_id, user)
        if row.status != "completed":
            raise HTTPException(409, "Only completed results can be reused")
        result = row.candidate if kind == "conversation" else row.result
        title, scope_id, run_id = parent.title, parent.id, row.id
        outcome = (
            ("candidate" if result else "conversation") if kind == "conversation" else "retained"
        )
        if kind == "conversation" and not result:
            messages = repository.messages(db, row.id)
            result = {"summary": messages[-1].content[:500] if messages else "本轮未形成诊断结论"}
        config = row.config
    else:
        run = active_record(db, InspectionRun, row.run_id)
        parent = active_record(db, InspectionPlan, row.plan_id)
        completed = (
            row.agent_status == "completed"
            if kind == "inspection_item"
            else row.kind == "agent" and row.execution_status == "completed"
        )
        if not completed:
            raise HTTPException(409, "Only completed Agent results can be reused")
        result = row.agent_result if kind == "inspection_item" else row.result
        object_id = row.record_id if kind == "inspection_item" else row.object_key
        title = f"{parent.name} · {object_id}"
        if kind == "inspection_check":
            title += " · " + result.get("name", row.check_id)
        scope_id, run_id = parent.id, run.id
        outcome = row.verdict if kind == "inspection_item" else row.status
        config = {
            "plan_version": run.plan_version,
            "run_kind": run.run_kind,
            "window_start": run.window_start.isoformat(),
            "window_end": run.window_end.isoformat(),
            "agent": run.snapshot.get("agent_config", {}),
            "skill": run.snapshot.get("skill", {}),
            "checks": run.snapshot.get("checks", {}),
        }
    if not result:
        raise HTTPException(409, "This run has no structured result")
    summary = {
        "kind": kind,
        "id": row.id,
        "scope_id": scope_id,
        "run_id": run_id,
        "title": title,
        "summary": str(result.get("summary", ""))[:500],
        "outcome": outcome,
        "created_at": row.created_at,
        "historical": True,
    }
    return row, parent, summary, result, config


def detail(db, user, kind, identifier, section="result", offset=0, limit=4000):
    row, _, summary, result, config = source_record(db, user, kind, identifier)
    if section == "messages":
        if kind != "conversation":
            raise HTTPException(422, "Messages belong to a private conversation")
        value = [
            {"role": m.role, "content": m.content, "created_at": m.created_at.isoformat()}
            for m in repository.messages(db, row.id)
        ]
    elif section == "result":
        value = result
    elif section == "configuration":
        value = config
    elif isinstance(row, InspectionCheckResult):
        value = [
            {
                "id": str(t.id),
                "tool": t.tool,
                "success": t.success,
                "observed_at": t.created_at.isoformat(),
                "data": t.data,
            }
            for t in repository.traces(db, row.id)
        ]
    else:
        value = row.evidence
    content = json.dumps(redact(value), ensure_ascii=False, default=str, indent=2)
    end = min(len(content), offset + limit)
    return ResultDetail.model_validate(
        {
            "record": summary,
            "section": section,
            "content": content[offset:end],
            "offset": offset,
            "next_offset": end if end < len(content) else None,
            "total_chars": len(content),
        }
    ).model_dump(mode="json")


def draft(db, user, kind, identifier, body):
    require(db, user, "knowledge:manage")
    row, parent, _, _, _ = source_record(db, user, kind, identifier)
    if kind == "conversation" and not row.candidate:
        raise HTTPException(409, "This conversation has no structured diagnosis to distill")
    source = f"agent-result:{kind}:{row.id}"
    repository.draft_lock(db, source)
    existing = repository.existing_draft(db, source)
    if existing:
        return existing
    if kind in {"diagnosis", "conversation"}:
        from app.services.diagnosis.tools import knowledge_scope

        applications, version_map = knowledge_scope(db, parent.id)
        versions = sorted({v for values in version_map.values() for v in values})
    else:
        applications = [str(parent.application_id)] if parent.application_id else []
        versions = []
    document = KnowledgeDocument(
        title=redact(body.title),
        content=redact(body.content),
        source=source,
        tags=list(dict.fromkeys(["agent-result", *redact(body.tags)]))[:20],
        enabled=False,
        owner_id=user.id,
        application_ids=list(map(str, applications)),
        applicable_versions=versions,
    )
    db.add(document)
    db.flush()
    audit(
        db,
        user,
        "agent_result_knowledge_drafted",
        "knowledge_document",
        document.id,
        {"source": source},
    )
    db.commit()
    return document


def tool_call(db, run, name, body, *, inspection=False):
    business = "inspection" if inspection else "diagnosis"
    user = active_user(db, run.requested_by)
    if user is None:
        raise ValueError("history_requester_unavailable")
    scope_id = run.plan_id if inspection else run.incident_id
    conversation_id = run.conversation_id if isinstance(run, AnalysisRun) else None
    if name == "search_agent_history":
        return listing(
            db,
            user,
            business,
            scope_id,
            **body.model_dump(),
            conversation_id=conversation_id,
            exclude_id=run.id,
        )
    row, _, summary, _, _ = source_record(db, user, body.kind, body.result_id)
    if (
        summary["scope_id"] != scope_id
        or (inspection and body.kind not in {"inspection_item", "inspection_check"})
        or (not inspection and body.kind not in {"diagnosis", "conversation"})
        or (body.kind == "conversation" and row.conversation_id != conversation_id)
        or (inspection and row.run_id == run.id)
    ):
        raise ValueError("history_outside_business_scope")
    return detail(db, user, body.kind, body.result_id, body.section, body.offset, body.limit)
