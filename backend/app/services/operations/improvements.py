from datetime import UTC, datetime

from fastapi import HTTPException

from app.models import (
    Application,
    ImprovementTask,
    KnowledgeDocument,
    KnowledgeSkill,
    PostmortemReport,
    RepairHandoff,
)
from app.repositories import improvements as repository
from app.repositories.notifications import active_user, admins, audit
from app.services.operations.notifications import enqueue
from app.services.pagination import page
from app.services.records import active_record
from app.utils.redaction import redact


def approved(db, identifier, number):
    row = active_record(db, PostmortemReport, identifier)
    db.refresh(row, with_for_update=True)
    if row.status != "approved" or row.revision != number:
        raise HTTPException(409, "Select the current approved report version")
    version = repository.revision(db, row.id, number)
    if not version:
        raise HTTPException(409, "Report revision is unavailable")
    return row, version


def import_items(db, identifier, body, user):
    repository.import_lock(db, identifier)
    report, version = approved(db, identifier, body.expected_revision)
    items = version.content.get("improvements", [])
    if any(index < 0 or index >= len(items) for index in body.item_indexes):
        raise HTTPException(422, "Improvement index is outside the approved report")
    rows = []
    for index in dict.fromkeys(body.item_indexes):
        row = repository.existing(db, report.id, report.revision, index)
        if not row:
            row = ImprovementTask(
                report_id=report.id,
                report_revision=report.revision,
                item_index=index,
                incident_id=report.incident_id,
                item=items[index],
                title=items[index]["action"],
                requested_by=user.id,
            )
            db.add(row)
            db.flush()
            audit(
                db,
                user,
                "improvement_imported",
                "improvement_task",
                row.id,
                {"report_revision": report.revision, "item_index": index},
            )
        rows.append(row)
    db.commit()
    return rows


def listing(db, response, report_id=None, status=None, offset=0, limit=50):
    return page(db, repository.tasks(report_id, status), response, offset, limit)


def editable(db, identifier, expected_version, user, deciding=False):
    row = active_record(db, ImprovementTask, identifier)
    db.refresh(row, with_for_update=True)
    if row.version != expected_version:
        raise HTTPException(409, "Improvement version changed")
    if not deciding and user.role.code != "admin" and row.assignee_id not in (None, user.id):
        raise HTTPException(403, "Only the assignee or Admin can change this task")
    if row.status in ("completed", "cancelled"):
        raise HTTPException(409, "Completed or cancelled task is read-only")
    return row


def update(db, identifier, body, user):
    row = editable(db, identifier, body.expected_version, user)
    if row.status == "awaiting_review":
        raise HTTPException(409, "Task is awaiting Admin acceptance")
    if body.assignee_id and not active_user(db, body.assignee_id, handler=True):
        raise HTTPException(422, "Choose an active Operator/Admin")
    if user.role.code != "admin" and body.assignee_id != user.id:
        raise HTTPException(403, "Operator can only assign this task to themselves")
    if body.status == "unassigned" and body.assignee_id:
        raise HTTPException(422, "Unassigned task cannot have an assignee")
    if body.status in ("pending", "in_progress") and not (
        body.assignee_id and body.due_at and body.acceptance.strip()
    ):
        raise HTTPException(422, "Assignee, deadline and acceptance criteria are required")
    if body.status == "cancelled" and user.role.code != "admin":
        raise HTTPException(403, "Admin cancels improvement tasks")
    for key, value in body.model_dump(exclude={"expected_version"}).items():
        setattr(row, key, redact(value))
    row.version += 1
    audit(
        db,
        user,
        "improvement_updated",
        "improvement_task",
        row.id,
        {"version": row.version, "status": row.status},
    )
    db.commit()
    return row


def submit(db, identifier, body, user):
    row = editable(db, identifier, body.expected_version, user)
    if row.status not in ("pending", "in_progress") or not active_user(
        db, row.assignee_id, handler=True
    ):
        raise HTTPException(409, "Assign an active owner before submitting")
    handoffs = []
    for identifier in dict.fromkeys(body.handoff_ids):
        handoff = active_record(db, RepairHandoff, identifier)
        if handoff.incident_id != row.incident_id:
            raise HTTPException(422, "Repair evidence belongs to another incident")
        handoffs.append(
            {
                "id": str(handoff.id),
                "packet_hash": handoff.packet_hash,
                "results": handoff.results,
                "verifications": handoff.verifications,
            }
        )
    row.submission = redact(
        {
            "result": body.result,
            "handoffs": handoffs,
            "submitted_by": str(user.id),
            "submitted_at": datetime.now(UTC).isoformat(),
        }
    )
    row.status, row.version = "awaiting_review", row.version + 1
    audit(
        db,
        user,
        "improvement_submitted",
        "improvement_task",
        row.id,
        {"submission": row.submission},
    )
    enqueue(
        db,
        [admin.id for admin in admins(db)],
        "improvement_review_" + str(row.version),
        "improvement_task",
        row.id,
        "整改待验收：" + row.title,
    )
    db.commit()
    return row


def decide(db, identifier, body, user):
    row = editable(db, identifier, body.expected_version, user, deciding=True)
    if row.status != "awaiting_review":
        raise HTTPException(409, "Task has not been submitted for acceptance")
    row.decision = redact(
        {
            "accepted": body.accepted,
            "evidence": body.evidence,
            "decided_by": str(user.id),
            "decided_at": datetime.now(UTC).isoformat(),
        }
    )
    row.status, row.version = "completed" if body.accepted else "in_progress", row.version + 1
    audit(db, user, "improvement_decided", "improvement_task", row.id, row.decision)
    db.commit()
    return row


def remind_due(db, now=None):
    from app.services.operations.evaluation import digest

    now = now or datetime.now(UTC)
    for row in repository.overdue(db, now):
        recipients = [user.id for user in admins(db)]
        owner = active_user(db, row.assignee_id, handler=True) if row.assignee_id else None
        if owner:
            recipients.append(owner.id)
        enqueue(
            db,
            recipients,
            "improvement_due_" + digest(row.due_at.isoformat())[:12],
            "improvement_task",
            row.id,
            "整改已到期：" + row.title,
        )
    db.commit()


def validity(db, kind, identifier, body, user):
    model = KnowledgeDocument if kind == "document" else KnowledgeSkill
    row = active_record(db, model, identifier)
    db.refresh(row, with_for_update=True)
    if row.version != body.expected_version:
        raise HTTPException(409, "Knowledge version changed")
    if body.owner_id and not active_user(db, body.owner_id, handler=True):
        raise HTTPException(422, "Choose an active Operator/Admin owner")
    for application_id in body.application_ids:
        active_record(db, Application, application_id)
    if any(not value.strip() or len(value) > 100 for value in body.applicable_versions):
        raise HTTPException(422, "Invalid applicable version")
    values = body.model_dump(
        mode="json", exclude={"expected_version", "review_at", "expires_at", "owner_id"}
    )
    row.application_ids, row.applicable_versions = (
        list(dict.fromkeys(values["application_ids"])),
        list(dict.fromkeys(values["applicable_versions"])),
    )
    row.owner_id, row.review_at, row.expires_at = body.owner_id, body.review_at, body.expires_at
    # Applicability does not change chunk content/version; the document revision still advances.
    row.version += 1
    if kind == "document":
        from app.services.knowledge.index import withdraw

        withdraw(db, row.id)
        row.index_status, row.error_code = "queued", None
    audit(
        db,
        user,
        "knowledge_validity_updated",
        "knowledge_" + kind,
        row.id,
        {"version": row.version},
    )
    db.commit()
    return row


def draft_knowledge(db, identifier, body, user):
    repository.import_lock(db, identifier)
    report, _ = approved(db, identifier, body.expected_revision)
    source = f"postmortem:{report.id}:v{report.revision}"
    row = repository.report_draft(db, source)
    if row:
        if row.enabled:
            raise HTTPException(409, "Knowledge from this report version is already published")
        return row
    row = KnowledgeDocument(
        title=redact(body.title),
        content=redact(body.content),
        source=source,
        enabled=False,
        owner_id=user.id,
    )
    db.add(row)
    db.flush()
    audit(db, user, "report_knowledge_drafted", "knowledge_document", row.id)
    db.commit()
    return row
