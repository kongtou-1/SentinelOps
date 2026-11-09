"""Frozen incident evidence, immutable revisions and explicit report publication."""

import hashlib
import json
from datetime import UTC, datetime

from fastapi import HTTPException

import app.repositories.reports.logic as _repository
from app.models import (
    AgentAnalysis,
    Approval,
    AuditLog,
    Incident,
    IncidentTimeline,
    PostmortemDelivery,
    PostmortemReport,
    RepairHandoff,
    SkillExecution,
)
from app.schemas.reports import ReportContent
from app.services.diagnosis.analyses import available, configuration
from app.services.executions.actions import user_valid
from app.services.incidents.logic import append_timeline, linked_alerts, lock_incidents
from app.services.records import active_record
from app.utils.canonical_json import digest, encoded
from app.utils.redaction import redact

CONTEXT_LIMIT = 256 * 1024


def report_audit(db, report, action, user=None, detail=None, request=None):
    content = redact({"report_id": str(report.id), "revision": report.revision, **(detail or {})})
    _repository.report_audit_query(
        action,
        content,
        db,
        report,
        request.client.host[:100] if request and request.client else None,
        user,
    )
    append_timeline(db, _repository.report_audit_query_2(db, report), action, content, actor=user)


def source_incidents(db, root):
    rows, seen, pending = [], set(), [root]
    while pending:
        row = pending.pop()
        if row.id in seen:
            continue
        seen.add(row.id)
        rows.append(row)
        _repository.source_incidents_query(db, pending, row)
    return sorted(rows, key=lambda r: (r.started_at, str(r.id)))


def complete_incident(db, incident):
    if incident.status != "closed":
        raise HTTPException(409, "Close the incident before generating a report")
    ids = [r.id for r in source_incidents(db, incident)]
    for model, statuses in (
        (AgentAnalysis, ("queued", "running")),
        (SkillExecution, ("queued", "prechecking", "executing", "verifying")),
    ):
        if _repository.complete_incident_query(db, ids, model, statuses):
            raise HTTPException(409, "Incident still has active diagnosis or execution tasks")


def snapshot(db, incident):
    incidents = source_incidents(db, incident)
    ids = [r.id for r in incidents]
    records = []

    def add(kind, row, fields):
        data = {name: getattr(row, name) for name in fields}
        data = json.loads(
            json.dumps(data, default=lambda v: v.isoformat() if isinstance(v, datetime) else str(v))
        )
        data = redact(data)
        records.append(
            {
                "id": kind + ":" + str(row.id),
                "kind": kind,
                "record_id": str(row.id),
                "occurred_at": data.get("occurred_at")
                or data.get("starts_at")
                or data.get("started_at")
                or row.created_at.isoformat(),
                "data": data,
                "content_hash": digest(data),
            }
        )

    for row in incidents:
        add(
            "incident",
            row,
            (
                "title",
                "status",
                "severity",
                "assignee_id",
                "started_at",
                "recovered_at",
                "closed_at",
                "closure",
                "merged_into_id",
            ),
        )
    for row in linked_alerts(db, incident.id):
        add(
            "alert",
            row,
            (
                "incident_id",
                "status",
                "severity",
                "starts_at",
                "ends_at",
                "labels",
                "annotations",
                "rule_snapshot",
                "recovery_status",
                "recovery_reason",
                "recovery_checked_at",
                "recovery_evidence_at",
                "acknowledged_at",
                "acknowledged_by",
                "comment",
                "server_id",
                "llm_endpoint_id",
                "prediction_task_id",
            ),
        )
    projections = (
        (
            RepairHandoff,
            "repair_handoff",
            (
                "incident_id",
                "analysis_id",
                "application_id",
                "requested_by",
                "packet",
                "packet_hash",
                "results",
                "verifications",
            ),
        ),
        (
            IncidentTimeline,
            "timeline",
            ("incident_id", "event_type", "occurred_at", "actor_type", "actor_id", "content"),
        ),
        (
            AgentAnalysis,
            "analysis",
            (
                "incident_id",
                "requested_by",
                "status",
                "started_at",
                "finished_at",
                "error_code",
                "result",
                "evidence",
                "source_context",
                "database_context",
                "knowledge",
            ),
        ),
        (
            Approval,
            "approval",
            (
                "incident_id",
                "analysis_id",
                "container_id",
                "requested_by",
                "approver_id",
                "status",
                "expires_at",
                "decided_at",
                "decision_comment",
                "error_code",
            ),
        ),
        (
            SkillExecution,
            "execution",
            (
                "incident_id",
                "approval_id",
                "container_id",
                "initiated_by",
                "status",
                "started_at",
                "dispatched_at",
                "finished_at",
                "precheck",
                "output",
                "verification",
                "error_code",
            ),
        ),
        (AuditLog, "audit", ("incident_id", "approval_id", "execution_id", "user_id", "action")),
    )
    for model, kind, fields in projections:
        query = _repository.snapshot_query(ids, model)
        if model is AuditLog:
            query = _repository.snapshot_query_2(query)
        elif model is IncidentTimeline:
            query = _repository.snapshot_query_3(query)
        for row in _repository.snapshot_query_4(db, model, query):
            add(kind, row, fields)
    gaps = []
    if any(
        row.closure.get("forced") and not row.closure.get("recovery_verified") for row in incidents
    ):
        gaps.append("管理员强制关闭事故；关闭时资源恢复未通过核验，关闭不代表修复成功")
    if any(r["kind"] == "alert" and r["data"]["recovery_status"] != "verified" for r in records):
        gaps.append("部分告警未通过资源恢复核验；通知结束或人工终止不代表恢复成功")
    if not any(r["kind"] == "analysis" and r["data"]["status"] == "completed" for r in records):
        gaps.append("没有成功的 AI 诊断记录，根因需人工确认")
    if not any(r["kind"] == "execution" for r in records):
        gaps.append("没有平台执行记录，不能据此推断执行动作或应用健康")
    evidence = {
        "incident_id": str(incident.id),
        "title": redact(incident.title),
        "captured_at": datetime.now(UTC).isoformat(),
        "records": records,
        "data_gaps": gaps,
    }
    from app.services.reports.resources import resource_context

    evidence["resource_context"] = {**resource_context(db, evidence), "source": "frozen"}
    return evidence


def generate(db, incident_id, user, force, request):
    lock_incidents(db)
    incident = active_record(db, Incident, incident_id)
    complete_incident(db, incident)
    existing = _repository.generate_query(db, incident_id)
    if existing:
        return existing, False
    if not force:
        existing = _repository.generate_query_2(db, incident_id)
        if existing:
            return existing, False
    available("report")
    from app.services.reports.context import model_context

    evidence = snapshot(db, incident)
    context = model_context(evidence)
    evidence["context_summary"] = {
        "version": 1,
        "snapshot_bytes": len(encoded(evidence)),
        "input_bytes": len(encoded(context)),
        "limit_bytes": CONTEXT_LIMIT,
        "input_records": len(context["records"]),
    }
    oversized = len(encoded(context)) > CONTEXT_LIMIT
    row = PostmortemReport(
        incident_id=incident_id,
        requested_by=user.id,
        snapshot=evidence,
        snapshot_hash=digest(evidence),
        config=configuration("report"),
        status="failed" if oversized else "queued",
        error_code="report_context_too_large" if oversized else None,
        finished_at=datetime.now(UTC) if oversized else None,
    )
    _repository.generate_query_3(db, row)
    report_audit(
        db,
        row,
        "report_failed" if oversized else "report_queued",
        user,
        {"error_code": row.error_code},
        request,
    )
    db.commit()
    _repository.generate_query_4(db, row)
    return row, True


def locked_report(db, identifier):
    lock_incidents(db)
    row = active_record(db, PostmortemReport, identifier)
    _repository.locked_report_query(db, row)
    if row.deleted_at:
        raise HTTPException(404, "Resource not found")
    return row


def delete_report(db, identifier, user, request):
    row = locked_report(db, identifier)
    active_delivery = _repository.delete_report_query(db, row)
    if row.status in ("queued", "running") or active_delivery:
        raise HTTPException(409, "报告正在生成或发送邮件，请等待任务结束后删除")
    row.deleted_at = row.updated_at = datetime.now(UTC)
    report_audit(db, row, "report_deleted", user, request=request)
    db.commit()


def revision(db, row):
    return _repository.revision_query(db, row)


def content_valid(row, value):
    content = ReportContent.model_validate(value)
    known = {r["id"] for r in row.snapshot["records"]}
    known.update(
        item["id"]
        for record in row.snapshot["records"]
        if record["kind"] == "analysis"
        for item in record["data"].get("evidence", [])
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    )
    refs = set(content.evidence_ids)
    for cause in content.causes:
        refs.update(cause.evidence_ids)
        if cause.confidence == "confirmed" and not cause.evidence_ids:
            raise ValueError("Confirmed causes require evidence")
    if not refs <= known:
        raise ValueError("Evidence references are outside the frozen snapshot")
    result = redact(content.model_dump(mode="json"))
    result["data_gaps"] = list(dict.fromkeys(result["data_gaps"] + row.snapshot["data_gaps"]))
    if len(encoded(result)) > 65536:
        raise ValueError("Report content is too large")
    return result


def add_revision(db, row, content, user=None):
    row.revision += 1
    _repository.add_revision_query(content, db, digest(content), row, user)


def edit(db, identifier, body, user, request):
    row = locked_report(db, identifier)
    if row.status != "draft" or row.revision != body.expected_revision:
        raise HTTPException(409, "Report is not an editable draft at this revision")
    try:
        content = content_valid(row, body.content.model_dump(mode="json"))
    except ValueError as exc:
        raise HTTPException(422, "Invalid report content or evidence references") from exc
    add_revision(db, row, content, user)
    row.reviewed_by = row.reviewed_at = row.review_comment = None
    report_audit(db, row, "report_edited", user, {"content_hash": digest(content)}, request)
    db.commit()
    _repository.edit_query(db, row)
    return row


def review(db, identifier, body, user, request):
    row = locked_report(db, identifier)
    if row.status != "draft" or row.revision != body.expected_revision:
        raise HTTPException(409, "Review requires the current draft revision")
    row.reviewed_by, row.reviewed_at, row.review_comment = (
        user.id,
        datetime.now(UTC),
        redact(body.comment),
    )
    if body.decision == "approve":
        row.status = "approved"
    report_audit(
        db,
        row,
        "report_approved" if body.decision == "approve" else "report_changes_requested",
        user,
        {"comment": row.review_comment},
        request,
    )
    db.commit()
    _repository.review_query(db, row)
    return row


def approved_export(db, row, format):
    from app.services.reports.exports import export_report

    if format == "pdf":
        frozen = _repository.approved_export_query(db, row)
        if frozen is not None:
            return frozen
    return export_report(row, revision(db, row).content, format)


def enqueue_delivery(db, identifier, body, user, request):
    from app.services.operations import mail_settings

    row = locked_report(db, identifier)
    request_hash = digest(
        {"report_id": str(row.id), "user_id": str(user.id), **body.model_dump(mode="json")}
    )
    previous = _repository.enqueue_delivery_query(body, db)
    if previous:
        if previous.request_hash != request_hash:
            raise HTTPException(409, "Idempotency key is bound to a different request")
        return previous
    if row.status != "approved" or row.revision != body.expected_revision:
        raise HTTPException(409, "Sending requires the current approved revision")
    if not user_valid(db, row.reviewed_by, admin=True):
        raise HTTPException(409, "Report reviewer is no longer an active Admin")
    attachment = approved_export(db, row, "pdf")
    sender = mail_settings.runtime(db, password=False)["sender"]
    delivery = PostmortemDelivery(
        report_id=row.id,
        revision=row.revision,
        requested_by=user.id,
        idempotency_key=body.idempotency_key,
        request_hash=request_hash,
        recipients=body.recipients,
        sender=sender,
        subject=f"事故复盘 {row.snapshot['title']} 第{row.revision}版".replace("\r", " ").replace(
            "\n", " "
        )[:300],
        body=(
            f"已审核事故复盘报告，事故 {row.incident_id}，报告 {row.id}，版本 {row.revision}。\n"
            f"审核人 {row.reviewed_by}，审核时间 {row.reviewed_at.isoformat()}。\n详见 PDF 附件。"
        ),
        comment=redact(body.comment),
        attachment=attachment,
        attachment_hash=hashlib.sha256(attachment).hexdigest(),
    )
    _repository.enqueue_delivery_query_2(db, delivery)
    report_audit(
        db,
        row,
        "report_mail_queued",
        user,
        {
            "delivery_id": str(delivery.id),
            "recipients": body.recipients,
            "comment": delivery.comment,
            "attachment_hash": delivery.attachment_hash,
        },
        request,
    )
    db.commit()
    _repository.enqueue_delivery_query_3(db, delivery)
    return delivery
