"""A read-only timeline reconstructed from committed run, check and tool records."""

import base64
import json
from datetime import datetime
from uuid import UUID

from fastapi import HTTPException

from app.models import InspectionCheckResult, InspectionItem, InspectionTrace
from app.repositories import inspection_progress as repository
from app.repositories.inspections import ACTIVE
from app.schemas.inspections import InspectionProgressEvent, InspectionProgressResponse
from app.utils.redaction import redact


def cursor(event):
    return base64.urlsafe_b64encode(
        json.dumps([event["occurred_at"].isoformat(), event["id"]]).encode()
    ).decode()


def decode_cursor(value):
    if not value:
        return None
    try:
        at, identifier = json.loads(base64.b64decode(value, altchars=b"-_", validate=True))
        at = datetime.fromisoformat(at)
        if not at.tzinfo or not isinstance(identifier, str) or not 1 <= len(identifier) <= 150:
            raise ValueError
        return at, identifier
    except (ValueError, TypeError):
        raise HTTPException(422, "Invalid inspection progress cursor") from None


def check_event(row, kind):
    return dict(
        object_key=row.object_key,
        check_name=row.result.get("name", row.check_id),
        status="running"
        if kind == "check_started"
        else ("error" if row.execution_status == "error" else row.status),
        summary=row.summary if kind == "check_finished" else "",
        error_code=row.error_code if kind == "check_finished" else None,
    )


def listing(db, run, before=None, limit=60):
    rows = db.execute(repository.references(run, decode_cursor(before), limit + 1)).mappings().all()
    has_more, rows = len(rows) > limit, rows[:limit]
    models = {
        "check_started": InspectionCheckResult,
        "check_finished": InspectionCheckResult,
        "tool": InspectionTrace,
        "object": InspectionItem,
        "legacy_tool": InspectionItem,
    }
    loaded = {
        model: repository.records(
            db, model, {UUID(row["record_id"]) for row in rows if models.get(row["kind"]) == model}
        )
        for model in set(models.values())
    }
    traces = list(loaded[InspectionTrace].values())
    trace_checks = repository.records(
        db, InspectionCheckResult, {t.check_result_id for t in traces}
    )
    events = []
    for row in reversed(rows):
        kind = row["kind"]
        value = dict(id=row["id"], occurred_at=row["occurred_at"], kind=kind, status="completed")
        record = loaded[models[kind]].get(row["record_id"]) if kind in models else None
        if kind in models and record is None:
            continue
        if kind.startswith("check_"):
            value.update(check_event(record, kind))
        elif kind == "object":
            value.update(object_key=record.record_id)
            if not run.snapshot.get("checks"):
                value.update(summary="规则检查已完成" if record.checks else "检查对象已就绪")
        elif kind == "tool":
            check = trace_checks.get(str(record.check_result_id))
            value.update(
                object_key=check.object_key if check else None,
                check_name=check.result.get("name", check.check_id) if check else None,
                tool=record.tool,
                status="success" if record.success else "error",
                data=record.data,
            )
        elif kind == "legacy_tool":
            call = next(c for c in record.evidence["tool_calls"] if c["id"] == row["call_id"])
            value.update(
                kind="tool",
                object_key=record.record_id,
                tool=call["tool"],
                status="success" if call["success"] else "error",
                data={
                    "arguments": call.get("arguments"),
                    "value": call.get("data"),
                    "code": call.get("code"),
                    "code_sha256": call.get("code_sha256"),
                },
            )
        elif kind == "finished":
            value.update(status=run.status, error_code=run.error_code)
        events.append(InspectionProgressEvent.model_validate(redact(value)))
    current = None
    if run.status in ACTIVE:
        record = repository.current(db, run)
        if record:
            value = (
                check_event(record, "check_started")
                if isinstance(record, InspectionCheckResult)
                else dict(object_key=record.record_id, check_name="Agent 检查", status="running")
            )
            current = InspectionProgressEvent(
                id="current:" + str(record.id),
                kind="check_started",
                occurred_at=getattr(record, "started_at", None) or record.created_at,
                **redact(value),
            )
    return InspectionProgressResponse(
        status=run.status,
        current=current,
        events=events,
        next_cursor=cursor(rows[-1]) if has_more else None,
    )
