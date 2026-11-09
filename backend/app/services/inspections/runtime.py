import re
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException

from app.db.session import SessionLocal
from app.models import InspectionItem, InspectionRun
from app.repositories import inspections as repository
from app.services import service_databases
from app.services.inspections import agent, assembly, plans, sources
from app.services.inspections.checkers import check
from app.services.inspections.outcomes import rule_status
from app.utils.redaction import redact


def current_run(db, run_id, lock=False):
    run = db.get(InspectionRun, run_id, populate_existing=True)
    if not run or run.deleted_at or run.status not in repository.ACTIVE:
        raise ValueError("inspection_stopped")
    plan = repository.plan(db, run.plan_id, lock=lock)
    if lock:
        db.refresh(run)
        if run.deleted_at or run.status not in repository.ACTIVE:
            raise ValueError("inspection_stopped")
    if not plan or plan.version != run.plan_version:
        raise ValueError("inspection_plan_changed")
    plans.validate_plan(db, plan)
    if plan.execution_mode == "checks":
        from app.services.inspections import checks

        checks.guard(db, plan, run)
    if plan.data_source_id:
        from app.services.inspections.data_sources import active

        source = active(db, plan.data_source_id)
        if source.version != run.snapshot.get("data_source_version"):
            raise ValueError("inspection_source_changed")
    for source_id, version in {
        **run.snapshot.get("related_source_versions", {}),
        **run.snapshot.get("agent_source_versions", {}),
    }.items():
        from app.services.inspections.data_sources import active

        source = active(db, source_id)
        if not source.enabled or source.version != version:
            raise ValueError("inspection_source_changed")
    if (
        run.started_at
        and (datetime.now(UTC) - run.started_at).total_seconds() >= plan.timeout_seconds
    ):
        raise ValueError("inspection_timeout")
    if run.trigger in {"manual", "event", "scan", "trial"}:
        from app.models import InspectionPlan
        from app.repositories.access import active_user
        from app.services.access_control import Authorization, record_allowed

        actor = active_user(db, run.requested_by)
        if (
            actor is None
            or not Authorization(db, actor).has("inspections:run")
            or not record_allowed(db, actor, InspectionPlan, plan.id, "inspections:run")
        ):
            raise ValueError("inspection_authorization_revoked")
    if (
        plan.source_kind == "database"
        and service_databases.frozen(sources.validate_database(db, plan))
        != run.snapshot["database"]
    ):
        raise ValueError("database_configuration_changed")
    for source in plan.related_sources or []:
        if source["source_kind"] == "database" and service_databases.frozen(
            sources.validate_database(db, sources.related_view(plan, source))
        ) != run.snapshot.get("related_databases", {}).get(source["name"]):
            raise ValueError("database_configuration_changed")
    return plan, run


def collect(db, plan, run):
    if plan.execution_mode == "agent":
        db.add(
            InspectionItem(
                run_id=run.id,
                plan_id=plan.id,
                record_id="workflow",
                record_version=str(plan.version),
                observed_at=datetime.now(UTC),
                identity_hash=service_databases.digest([str(run.id), "workflow", str(plan.id)]),
                evidence={"facts": {"parameters": redact(plan.agent_parameters)}, "tool_calls": []},
                checks=[],
                verdict="unassessable",
                agent_status="pending",
            )
        )
        run.status, run.collected = "evaluating", 1
        run.summary = {"source_complete": False}
        db.commit()
        return
    remaining = plan.max_records - run.collected
    records, cursor = sources.read_page(
        db, plan, run, min(remaining, 1 if plan.related_sources else 50)
    )
    # Recheck authorization after external IO, before persisting any returned data.
    db.expire_all()
    plan, run = current_run(db, run.id)

    def guard():
        db.expire_all()
        current_run(db, run.id)

    records = [assembly.compose(db, plan, run, record, guard) for record in records]
    db.expire_all()
    plan, run = current_run(db, run.id, lock=True)
    for record in records:
        value = redact(record.model_dump(mode="json"))
        identity = service_databases.digest([str(run.id), record.id, record.version])
        if repository.item_exists(db, identity):
            continue
        try:
            checks, verdict = check(value, plan.rules)
        except Exception:
            raise ValueError("inspection_rule_failed") from None
        db.add(
            InspectionItem(
                run_id=run.id,
                plan_id=plan.id,
                identity_hash=identity,
                record_id=redact(record.id),
                record_version=redact(record.version),
                observed_at=datetime.now(UTC),
                evidence=value,
                checks=checks,
                verdict=verdict,
                rule_verdict=rule_status(checks),
                agent_status="pending" if plan.agent_enabled else "disabled",
            )
        )
        db.flush()
    run.collected += len(records)
    run.page_cursor = cursor
    if not cursor or run.collected >= plan.max_records:
        run.status = "evaluating"
        run.summary = {"source_complete": not bool(cursor)}
    db.commit()


def evaluate_item(db, plan, run, item):
    item_id, run_id = item.id, run.id
    if item.agent_status == "running":
        item.agent_status = "failed"
        item.agent_result = {"error_code": "inspection_agent_interrupted"}
        db.commit()
        return
    if run.agent_calls >= plan.max_agent_items:
        item.agent_status = "skipped_budget"
        db.commit()
        return
    run.agent_calls += 1
    item.agent_status = "running"
    db.commit()
    result, error = None, None
    try:
        result = agent.evaluate(run_id, item_id)
    except Exception as exc:
        candidate = str(exc) if isinstance(exc, ValueError) else "inspection_agent_failed"
        error = candidate if re.fullmatch(r"[a-z_]{1,80}", candidate) else "inspection_agent_failed"
    db.expire_all()
    _, run = current_run(db, run_id, lock=True)
    item = db.get(InspectionItem, item_id, populate_existing=True)
    if error:
        item.agent_status, item.agent_result = "failed", {"error_code": error}
    else:
        item.agent_status, item.agent_result = "completed", result
        if plan.execution_mode == "agent":
            from app.services.inspections.workflow import persist_findings

            persist_findings(db, plan, run, item, result)
        elif not item.checks:
            item.verdict = result["verdict"]
    db.commit()


def finish(db, plan, run):
    pending = db.scalars(repository.items(run.id)).all()
    incomplete_agent = any(i.agent_status in {"failed", "skipped_budget"} for i in pending)
    source_complete = run.summary.get("source_complete", False)
    run.summary = {
        **run.summary,
        "source_complete": source_complete,
        "counts": repository.counts(db, run.id),
        "health_counts": repository.health_counts(db, [run.id]).get(run.id, {}),
        "agent_complete": not incomplete_agent,
        "agent_failed": sum(i.agent_status == "failed" for i in pending),
        "agent_skipped": sum(i.agent_status == "skipped_budget" for i in pending),
        "unique_records": len(pending),
        "duplicate_records": run.collected - len(pending),
        "evaluated_scope": "configured_rules_and_supplied_evidence",
    }
    run.status = "completed" if source_complete else "partial"
    run.error_stage = "agent" if incomplete_agent else None
    run.error_code = (
        "inspection_workflow_incomplete"
        if plan.execution_mode == "agent" and not source_complete
        else "record_budget_exhausted"
        if not source_complete
        else ("agent_incomplete" if incomplete_agent else None)
    )
    run.finished_at = datetime.now(UTC)
    if source_complete or plan.execution_mode == "agent":
        plan.watermark, plan.continuation = run.window_end, {}
    else:
        plan.continuation = {
            "start": run.window_start.isoformat(),
            "end": run.window_end.isoformat(),
            "cursor": run.page_cursor,
        }
    db.commit()


def run_once():
    with SessionLocal() as db:
        row = repository.runnable(db)
        if not row:
            return False
        identifier = row.id
        try:
            plan, run = current_run(db, identifier)
            if run.status == "queued":
                run.status, run.started_at = "collecting", datetime.now(UTC)
                db.commit()
            if plan.execution_mode == "checks":
                from app.services.inspections import checks_runtime

                checks_runtime.advance(db, plan, run)
                return True
            if run.status == "collecting":
                collect(db, plan, run)
            else:
                item = repository.pending_item(db, run.id)
                if item:
                    evaluate_item(db, plan, run, item)
                else:
                    # Finalization serializes with edits, pause, delete and manual starts.
                    plan, run = current_run(db, identifier, lock=True)
                    finish(db, plan, run)
        except Exception as exc:
            db.rollback()
            row = db.get(InspectionRun, identifier, populate_existing=True)
            if row and row.status in repository.ACTIVE:
                code = (
                    str(exc.detail)
                    if isinstance(exc, HTTPException)
                    else (
                        str(exc) if isinstance(exc, ValueError) else "inspection_source_unavailable"
                    )
                )
                row.error_code = (
                    code if re.fullmatch(r"[a-z_]{1,80}", code) else "inspection_source_invalid"
                )
                row.error_stage = (
                    "timeout"
                    if row.error_code == "inspection_timeout"
                    else "rule"
                    if row.error_code == "inspection_rule_failed"
                    else "collection"
                    if row.status in {"queued", "collecting"}
                    else "execution"
                )
                plan = repository.plan(db, row.plan_id)
                retryable = row.error_code in {
                    "source_http_error",
                    "inspection_source_unavailable",
                    "database_unavailable",
                    "query_timeout",
                    "source_timeout",
                }
                if plan and retryable and row.retry_count < plan.max_retries:
                    row.retry_count += 1
                    row.retry_at = datetime.now(UTC) + timedelta(seconds=5 * row.retry_count)
                    db.commit()
                    return True
                row.status = "failed"
                if row.snapshot.get("checks"):
                    from app.services.inspections import checks_runtime

                    checks_runtime.interrupted(db, row, row.error_code)
                row.finished_at = datetime.now(UTC)
                db.commit()
        return True
