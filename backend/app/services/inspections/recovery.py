"""Explicit recovery over the recorded observation; never repeat completed checks."""

from copy import deepcopy
from datetime import UTC, datetime

from fastapi import HTTPException
from sqlalchemy import select

from app.models import InspectionCheckResult, InspectionItem, InspectionRun
from app.repositories import inspections as repository
from app.services import service_databases
from app.services.inspections import checks, checks_runtime, plans
from app.services.records import active_record


def recover_interrupted(db):
    """Called only after acquiring the exclusive inspection worker lock."""
    rows = db.scalars(
        select(InspectionRun).where(
            InspectionRun.deleted_at.is_(None),
            InspectionRun.status.in_(("collecting", "evaluating")),
        )
    ).all()
    for run in rows:
        repository.plan(db, run.plan_id, lock=True)
        db.refresh(run)
        if run.status not in repository.ACTIVE:
            continue
        run.status = "failed"
        run.error_code, run.error_stage = "inspection_worker_interrupted", "execution"
        run.finished_at = datetime.now(UTC)
        if run.snapshot.get("checks"):
            checks_runtime.interrupted(db, run, run.error_code)
    db.commit()
    return len(rows)


def resume(db, identifier, user):
    from app.repositories.notifications import audit

    original = active_record(db, InspectionRun, identifier)
    plan = repository.plan(db, original.plan_id, lock=True)
    db.refresh(original)
    if not plan or plan.trial_only or not original.snapshot.get("checks"):
        raise HTTPException(409, "inspection_resume_unavailable")
    if original.status not in {"partial", "failed", "cancelled"}:
        raise HTTPException(409, "inspection_resume_unavailable")
    if plan.version != original.plan_version:
        raise HTTPException(409, "inspection_plan_version_changed")
    # A cursor alone cannot guarantee the same dataset after source changes.
    if not original.summary.get("source_complete"):
        raise HTTPException(409, "inspection_resume_discovery_incomplete")
    plans.validate_plan(db, plan)
    checks.guard(db, plan, original)
    if repository.active_run(db, plan.id):
        raise HTTPException(409, "inspection_run_active")
    previous = db.scalars(repository.check_results(original.id)).all()
    if not any(r.execution_status != "completed" for r in previous):
        raise HTTPException(409, "inspection_resume_nothing_pending")
    snapshot = deepcopy(original.snapshot)
    snapshot["resume"] = {
        "parent_run_id": str(original.id),
        "observation": "original_evidence",
        "original_window_end": original.window_end.isoformat(),
    }
    run = InspectionRun(
        plan_id=plan.id,
        plan_version=plan.version,
        requested_by=user.id,
        trigger="manual",
        status="evaluating",
        snapshot=snapshot,
        window_start=original.window_start,
        window_end=original.window_end,
        collected=original.collected,
        started_at=datetime.now(UTC),
        summary={"source_complete": True, "resumed_from": str(original.id)},
    )
    db.add(run)
    db.flush()
    items = {}
    for item in db.scalars(repository.items(original.id)).all():
        copied = InspectionItem(
            run_id=run.id,
            plan_id=plan.id,
            record_id=item.record_id,
            record_version=item.record_version,
            observed_at=item.observed_at,
            evidence=deepcopy(item.evidence),
            verdict="unassessable",
            check_status="unknown",
            identity_hash=service_databases.digest([str(run.id), item.record_id]),
        )
        db.add(copied)
        db.flush()
        items[item.id] = copied.id
    for old in previous:
        if old.item_id and old.item_id not in items:
            raise HTTPException(409, "inspection_resume_evidence_missing")
        completed = old.execution_status == "completed"
        copied = InspectionCheckResult(
            run_id=run.id,
            plan_id=plan.id,
            item_id=items.get(old.item_id),
            object_key=old.object_key,
            check_id=old.check_id,
            kind=old.kind,
            scope=old.scope,
            execution_status="completed" if completed else "pending",
            status=old.status if completed else "unknown",
            severity=old.severity if completed else None,
            summary=old.summary if completed else "",
            result={**deepcopy(old.result), "inherited_result_id": str(old.id)}
            if completed
            else {"name": old.result.get("name", old.check_id)},
            started_at=old.started_at if completed else None,
            finished_at=old.finished_at if completed else None,
        )
        db.add(copied)
    db.flush()
    for result in db.scalars(repository.check_results(run.id)).all():
        checks_runtime.refresh_object(db, result)
    audit(
        db,
        user,
        "inspection_resumed",
        "inspection_run",
        run.id,
        {"parent_run_id": str(original.id)},
    )
    db.commit()
    return run
