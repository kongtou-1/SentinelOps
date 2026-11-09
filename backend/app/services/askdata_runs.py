"""Ask-data conversations, run submission, durable events and CSV export."""

from __future__ import annotations

import asyncio
import csv
import io
import json

from fastapi import HTTPException, Request
from fastapi.responses import Response, StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy.exc import IntegrityError

from app.db.session import SessionLocal
from app.repositories import askdata_runs as repository
from app.schemas.askdata_runs import (
    AskDataConversationDetail,
    AskDataConversationInput,
    AskDataConversationResponse,
    AskDataEventPage,
    AskDataExecutionContext,
    AskDataResult,
    AskDataRunDetail,
    AskDataRunInput,
    AskDataRunResponse,
    AskDataRunSummary,
    AskDataStepEvent,
)
from app.services import access_control
from app.services import askdata as settings

# Upstream node names are stable within the pinned revision. Labels are short
# and never include the agent's internal reasoning text.
STEP_LABELS = {
    "question_intent": "识别问题意图",
    "question_extraction": "理解问题",
    "prepare_prediction_graph": "准备预测",
    "kumo_predict": "准备预测",
    "retrieve_candidates": "检索候选对象",
    "information_agent": "依据元数据回答",
    "prepare_candidates": "整理候选对象",
    "refine_evidence": "补充业务依据",
    "precheck_combined": "校验关联与筛选值",
    "construct_sql_from_candidates": "生成 SQL",
    "reconstruct_sql": "修正 SQL",
    "validate_sql_values": "校验筛选值",
    "validate_sql_query": "校验 SQL 与意图",
    "execute_sql_query": "执行查询",
    "check_empty_like_result": "检查查询结果",
    "check_value_repair": "校验枚举值",
    "format_and_respond": "整理回答",
    "unconstructable_sql_response": "无法构造 SQL",
    "queued": "排队中",
    "running": "开始问数",
    "executing_result": "执行结果查询",
    "result": "生成回答",
    "completed": "问数完成",
    "failed": "问数失败",
    "cancelled": "已取消",
    "interrupted": "已中断",
    "needs_clarification": "需要补充信息",
}

STEP_STATUS = {
    "start": "running",
    "end": "completed",
    "sql": "completed",
    "result": "completed",
    "error": "failed",
    "context": "completed",
    "queued": "queued",
    "running": "running",
}


def label(step: str) -> str:
    return STEP_LABELS.get(step, step)


def _event(row) -> AskDataStepEvent:
    data = row.data or {}
    step = data.get("step", row.kind)
    status = data.get("status", STEP_STATUS.get(row.kind, "completed"))
    return AskDataStepEvent(
        seq=row.seq,
        step=step,
        status=status,
        time=row.created_at,
        summary=data.get("summary") or label(step),
    )


def _authorize(db, user, row):
    access_control.require(
        db, user, "askdata:read", "service_database", row.connection_id
    )


def access_run(db, identifier, user):
    row = repository.run(db, identifier, user.id)
    if row is None:
        raise HTTPException(404, "问数运行不存在")
    _authorize(db, user, row)
    return row


def _check_capabilities():
    from app.core.config import get_settings
    from app.services.agent_config import profile

    if not get_settings().ai_enabled:
        raise HTTPException(503, "智能问数需要启用 AI")
    config = profile("diagnosis")
    if not config["enabled"] or not config["channels"]:
        raise HTTPException(503, "智能问数需要可用的模型通道")


def listing(db, user, connection_id=None, limit=50, offset=0):
    rows = repository.conversations(db, user.id, connection_id, limit, offset)
    return [AskDataConversationResponse.model_validate(row) for row in rows]


def create_conversation(db, user, body: AskDataConversationInput):
    connection = settings.authorized_connection(
        db, user, body.connection_id, "askdata:run"
    )
    if repository.active_version(db, connection.id) is None:
        raise HTTPException(409, "该连接尚未启用业务模型，请先完成构建并启用")
    row = repository.create_conversation(db, connection.id, user.id, body.title)
    db.commit()
    db.refresh(row)
    return AskDataConversationResponse.model_validate(row)


def detail(db, user, identifier):
    conversation = repository.conversation(db, identifier, user.id)
    if conversation is None:
        raise HTTPException(404, "问数会话不存在")
    _authorize(db, user, conversation)
    rows = repository.runs(db, conversation.id)
    return AskDataConversationDetail(
        **AskDataConversationResponse.model_validate(conversation).model_dump(),
        runs=[AskDataRunSummary.model_validate(row) for row in rows],
    )


def delete_conversation(db, user, identifier):
    conversation = repository.conversation(db, identifier, user.id, lock=True)
    if conversation is None:
        raise HTTPException(404, "问数会话不存在")
    access_control.require(db, user, "askdata:run", "service_database", conversation.connection_id)
    for run in repository.delete_conversation(db, conversation):
        repository.append_event(
            db, run.id, "step",
            {"step": "cancelled", "status": "completed", "summary": label("cancelled")},
        )
    db.commit()


def _context(db, connection, version_id):
    return AskDataExecutionContext(
        connection_id=connection.id,
        application_id=connection.application_id,
        environment=connection.environment,
        schema_hash=connection.schema_hash,
        config_version=connection.config_version,
        model_version_id=version_id,
        source_snapshot_id=repository.model_version_source(db, version_id),
    )


def submit(db, user, conversation_id, body: AskDataRunInput):
    # Serialize submission and deletion so no run can be queued into a deleted chat.
    conversation = repository.conversation(db, conversation_id, user.id, lock=True)
    if conversation is None:
        raise HTTPException(404, "问数会话不存在")
    _authorize(db, user, conversation)
    existing = repository.run_by_key(db, conversation.id, body.idempotency_key)
    if existing is not None:
        return AskDataRunResponse.model_validate(existing)

    connection = settings.authorized_connection(
        db, user, conversation.connection_id, "askdata:run"
    )
    version_id = repository.active_version(db, connection.id)
    if version_id is None:
        raise HTTPException(409, "该连接尚未启用业务模型，请先完成构建并启用")
    settings.validate_version(db, user, connection.id, version_id, "askdata:run")
    settings.require_active()
    _check_capabilities()

    context = _context(db, connection, version_id)
    row = repository.create_run(
        db, conversation, version_id, body.question.strip(), body.idempotency_key
    )
    try:
        db.flush()
        repository.append_event(db, row.id, "context", json.loads(context.model_dump_json()))
        repository.append_event(
            db,
            row.id,
            "step",
            {"step": "queued", "status": "queued", "summary": label("queued")},
        )
        db.commit()
    except IntegrityError:
        # A concurrent duplicate submission wins the unique constraint; the
        # caller gets the original run instead of a second execution.
        db.rollback()
        existing = repository.run_by_key(db, conversation.id, body.idempotency_key)
        if existing is None:
            raise
        return AskDataRunResponse.model_validate(existing)
    db.refresh(row)
    return AskDataRunResponse.model_validate(row)


def run_detail(db, user, identifier):
    row = access_run(db, identifier, user)
    result = AskDataResult.model_validate(row.result) if row.result else None
    raw_context = repository.context_event(db, row.id)
    context = AskDataExecutionContext.model_validate(raw_context) if raw_context else None
    events = [
        _event(item)
        for item in repository.events_after(db, row.id, 0, 500)
        if item.kind != "context"
    ]
    return AskDataRunDetail(
        **AskDataRunResponse.model_validate(row).model_dump(),
        result=result,
        context=context,
        events=events,
    )


def events(db, user, identifier, after_seq=0, limit=100):
    row = access_run(db, identifier, user)
    rows = repository.events_after(db, row.id, after_seq, limit)
    items = [_event(item) for item in rows if item.kind != "context"]
    return AskDataEventPage(
        items=items,
        latest_seq=max((item.seq for item in rows), default=after_seq),
        terminal=repository.terminal(row.status),
    )


def cancel(db, user, identifier):
    row = access_run(db, identifier, user)
    if repository.terminal(row.status):
        return AskDataRunResponse.model_validate(row)
    if not repository.cancel(db, row.id):
        return AskDataRunResponse.model_validate(row)
    repository.append_event(
        db,
        row.id,
        "step",
        {"step": "cancelled", "status": "completed", "summary": label("cancelled")},
    )
    db.commit()
    db.refresh(row)
    return AskDataRunResponse.model_validate(row)


def export_csv(db, user, identifier):
    row = access_run(db, identifier, user)
    if row.status != "completed" or not row.result:
        raise HTTPException(409, "该运行没有可导出的结果")
    result = AskDataResult.model_validate(row.result)
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow([column.name for column in result.columns])
    for values in result.rows:
        writer.writerow(["" if value is None else value for value in values])
    # Only the authorized rows actually returned by this run are exported.
    return Response(
        content="\ufeff" + buffer.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="askdata-{row.id}.csv"'},
    )


def stream(identifier, request: Request, db, user, after_seq=0):
    access_run(db, identifier, user)
    authorization = request.headers.get("authorization", "")
    db.close()

    async def generate():
        cursor = after_seq
        ticks = 0
        while not await request.is_disconnected():
            try:
                with SessionLocal() as session:
                    credentials = HTTPAuthorizationCredentials(
                        scheme="Bearer", credentials=authorization.removeprefix("Bearer ")
                    )
                    from app.services.authentication import get_current_session, get_current_user

                    auth_session = get_current_session(session, credentials)
                    current = get_current_user(session, auth_session)
                    access_run(session, identifier, current)
                    batch = repository.events_after(session, identifier, cursor, 100)
                    status = repository.run_status(session, identifier)
                    frames = [
                        (item.seq, _event(item).model_dump_json())
                        for item in batch
                        if item.kind != "context"
                    ]
            except HTTPException as exc:
                kind = "auth_required" if exc.status_code == 401 else "permission_lost"
                yield f"event: {kind}\ndata: {{}}\n\n"
                return
            except Exception:
                yield "event: connection_error\ndata: {}\n\n"
                return
            for sequence, payload in frames:
                yield f"id: {sequence}\nevent: event\ndata: {payload}\n\n"
            if batch:
                cursor = max(item.seq for item in batch)
            if status and repository.terminal(status) and not batch:
                yield "event: done\ndata: {}\n\n"
                return
            ticks += 1
            if ticks % 20 == 0:
                yield ": heartbeat\n\n"
            await asyncio.sleep(0.5)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
