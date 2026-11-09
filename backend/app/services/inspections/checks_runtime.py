"""Object/check execution ledger. Legacy inspection runs retain their original semantics."""

import json
from datetime import UTC, datetime

from sqlalchemy import select

from app.models import InspectionCheckResult, InspectionItem
from app.repositories import inspections as repository
from app.services import service_databases
from app.services.inspections import checks, sources
from app.services.inspections.checkers import check
from app.utils.redaction import redact


def aggregate(statuses, operator="and"):
    values = set(statuses)
    if operator == "or":
        return "pass" if "pass" in values else "fail" if values == {"fail"} else "unknown"
    return "fail" if "fail" in values else "pass" if values == {"pass"} else "unknown"


def add_results(db, plan, run, item=None, scope="object"):
    for definition in run.snapshot["checks"]["definitions"]:
        if definition["enabled"] and definition["scope"] == scope:
            db.add(
                InspectionCheckResult(
                    run_id=run.id,
                    plan_id=plan.id,
                    item_id=item.id if item else None,
                    object_key=item.record_id if item else "@collection",
                    check_id=definition["check_id"],
                    kind=definition["kind"],
                    scope=scope,
                    result={"name": definition["name"]},
                )
            )


def collect(db, plan, run):
    from app.schemas.inspections import InspectionRecord
    from app.services.inspections.runtime import current_run

    remaining = plan.max_records - run.collected
    if plan.object_mode == "fixed":
        offset = run.page_cursor.get("offset", 0)
        values = run.snapshot["plan"]["fixed_objects"][offset : offset + min(remaining, 50)]
        records = [
            InspectionRecord(
                id=obj["id"],
                version=service_databases.digest(obj["facts"]),
                facts=obj["facts"],
                updated_at=datetime.now(UTC),
            )
            for obj in values
        ]
        cursor = (
            {"offset": offset + len(values)}
            if offset + len(values) < len(plan.fixed_objects)
            else {}
        )
    else:
        records, cursor = sources.read_page(db, plan, run, min(remaining, 50))
    db.expire_all()
    plan, run = current_run(db, run.id, lock=True)
    seen = set(db.scalars(select(InspectionItem.record_id).where(InspectionItem.run_id == run.id)))
    for record in records:
        if record.id in seen:
            raise ValueError("inspection_duplicate_object")
        seen.add(record.id)
        item = InspectionItem(
            run_id=run.id,
            plan_id=plan.id,
            record_id=record.id,
            record_version=record.version,
            identity_hash=service_databases.digest([str(run.id), record.id]),
            observed_at=datetime.now(UTC),
            evidence=redact(record.model_dump(mode="json")),
            verdict="unassessable",
            check_status="unknown",
        )
        db.add(item)
        db.flush()
        add_results(db, plan, run, item)
    visited = run.summary.get("discovery_cursors", [])
    signature = service_databases.digest(cursor) if cursor else None
    if cursor and (cursor == run.page_cursor or signature in visited):
        raise ValueError("source_pagination_invalid")
    run.collected += len(records)
    run.page_cursor = cursor
    run.summary = {
        **run.summary,
        "source_complete": not bool(cursor),
        "discovered": run.collected,
        "discovery_cursors": [*visited, signature] if signature else visited,
    }
    if not cursor or run.collected >= plan.max_records:
        run.status = "evaluating"
        add_results(db, plan, run, scope="collection")
    db.commit()


def context(db, run, result):
    if result.item_id:
        item = db.get(InspectionItem, result.item_id)
        return {"object_id": item.record_id, **item.evidence}
    items = db.scalars(repository.items(run.id)).all()
    value = {
        "object_id": "@collection",
        "facts": {"objects": [{"id": item.record_id, **item.evidence} for item in items]},
    }
    if len(json.dumps(value, allow_nan=False).encode()) > 128 * 1024:
        raise ValueError("inspection_collection_input_limit")
    return value


def refresh_object(db, result):
    if not result.item_id:
        return
    rows = db.scalars(
        select(InspectionCheckResult).where(
            InspectionCheckResult.item_id == result.item_id,
            InspectionCheckResult.deleted_at.is_(None),
        )
    ).all()
    item = db.get(InspectionItem, result.item_id)
    item.check_status = aggregate(row.status for row in rows)
    item.rule_verdict = aggregate(row.status for row in rows if row.kind == "rule")
    item.checks = [
        {
            "code": row.check_id,
            "title": row.result.get("name", row.check_id),
            "status": row.status,
            "execution_status": row.execution_status,
            "summary": row.summary,
        }
        for row in rows
    ]
    agent_rows = [row for row in rows if row.kind == "agent"]
    item.agent_status = (
        "completed"
        if agent_rows and all(row.execution_status == "completed" for row in agent_rows)
        else "failed"
        if any(row.execution_status == "error" for row in agent_rows)
        else "pending"
        if agent_rows
        else "disabled"
    )
    item.verdict = {"pass": "supported", "fail": "contradicted", "unknown": "unassessable"}[
        item.check_status
    ]
    item.agent_result = {
        "summary": "；".join(row.summary for row in rows if row.status != "pass")[:4000]
        or "全部检查通过"
    }


def evaluate(db, plan, run, row):
    from app.services.inspections.runtime import current_run

    definition = checks.definition(run, row.check_id)
    if row.execution_status == "running":
        row.execution_status, row.error_code = "error", "inspection_check_interrupted"
        row.finished_at = datetime.now(UTC)
        refresh_object(db, row)
        db.commit()
        return
    row.execution_status, row.started_at = "running", datetime.now(UTC)
    db.commit()
    try:
        if row.scope == "collection" and not run.summary.get("source_complete"):
            raise ValueError("inspection_collection_incomplete")
        if row.kind == "rule":
            value = context(db, run, row)
            values, _ = check(value, [r.model_dump(mode="json") for r in definition.conditions])
            # Wildcard instances of the same condition must all satisfy that condition.
            states = [
                aggregate(v["status"] for v in values if v["code"] == condition.code)
                for condition in definition.conditions
            ]
            row.status = aggregate(states, definition.operator)
            row.summary = {
                "pass": "规则检查通过",
                "fail": "规则检查未通过",
                "unknown": "资料不足，无法判定",
            }[row.status]
            row.result = {"name": definition.name, "conditions": values}
            row.execution_status = "completed"
        else:
            if run.agent_calls >= plan.max_agent_items:
                raise ValueError("inspection_agent_budget_exhausted")
            run.agent_calls += 1
            db.commit()
            from app.services.inspections.check_agent import evaluate as evaluate_agent

            evaluate_agent(run.id, row.id)
            db.expire_all()
            _, run = current_run(db, run.id, lock=True)
            row = db.get(InspectionCheckResult, row.id, populate_existing=True)
            if row.execution_status != "completed":
                raise ValueError("inspection_result_not_submitted")
        with db.no_autoflush:
            current_run(db, run.id, lock=True)
        row.severity = definition.severity if row.status == "fail" else None
    except Exception as exc:
        import re

        db.rollback()
        _, run = current_run(db, run.id, lock=True)
        row = db.get(InspectionCheckResult, row.id, populate_existing=True)
        if row.execution_status != "completed":
            code = str(exc) if isinstance(exc, ValueError) else "inspection_check_failed"
            row.error_code = (
                code if re.fullmatch(r"[a-z_]{1,80}", code) else "inspection_check_failed"
            )
            row.execution_status, row.status = "error", "unknown"
            row.summary = "检查未完成，请查看执行错误"
    row.finished_at = datetime.now(UTC)
    db.flush()
    refresh_object(db, row)
    db.commit()


def coverage(db, run):
    groups = repository.check_counts(db, run.id)
    states = {status for status, _, _, _ in groups}
    counts = {
        state: sum(count for status, _, _, count in groups if status == state)
        for state in ("pass", "fail", "unknown")
    }
    completed = sum(count for _, execution, _, count in groups if execution == "completed")
    total = sum(count for _, _, _, count in groups)
    definitions = [c for c in run.snapshot["checks"]["definitions"] if c["enabled"]]
    objects_expected = (
        len(run.snapshot["plan"]["fixed_objects"])
        if run.snapshot["plan"]["object_mode"] == "fixed"
        else run.collected
        if run.summary.get("source_complete")
        else None
    )
    sample_complete = (
        run.run_kind == "trial" and run.collected >= run.snapshot["plan"]["max_records"]
    )
    if run.run_kind == "trial":
        if objects_expected is not None:
            objects_expected = min(objects_expected, run.snapshot["plan"]["max_records"])
        elif sample_complete:
            objects_expected = run.collected
    expected = (objects_expected if objects_expected is not None else run.collected) * sum(
        c["scope"] == "object" for c in definitions
    ) + sum(c["scope"] == "collection" for c in definitions)
    return {
        "discovery_complete": bool(run.summary.get("source_complete")),
        "objects_discovered": run.collected,
        "objects_expected": objects_expected,
        "checks_expected": expected,
        "expected_is_final": objects_expected is not None,
        "scope_is_sample": run.run_kind == "trial",
        "checks_completed": completed,
        "checks_errors": sum(count for _, execution, _, count in groups if execution == "error"),
        "checks_unfinished": expected - completed,
        "check_counts": counts,
        "collection_checks": sum(count for _, _, scope, count in groups if scope == "collection"),
        "conclusion": "fail"
        if "fail" in states
        else aggregate(states)
        if total and (run.summary.get("source_complete") or sample_complete) and completed == total
        else "unknown",
        "no_objects": run.collected == 0 and bool(run.summary.get("source_complete")),
    }


def advance(db, plan, run):
    if run.status == "collecting":
        collect(db, plan, run)
        return
    pending = db.scalars(
        repository.check_results(run.id)
        .where(InspectionCheckResult.execution_status.in_(("pending", "running")))
        .limit(50)
    ).all()
    if pending:
        for row in pending:
            from app.services.inspections.runtime import current_run

            plan, run = current_run(db, run.id)
            evaluate(db, plan, run, row)
            if row.kind == "agent":
                break
        return
    from app.services.inspections.runtime import current_run

    plan, run = current_run(db, run.id, lock=True)
    value = coverage(db, run)
    run.summary = {
        **run.summary,
        "coverage": value,
        "health_counts": repository.health_counts(db, [run.id]).get(run.id, {}),
    }
    scope_complete = value["discovery_complete"] or (
        run.run_kind == "trial" and run.collected >= plan.max_records
    )
    run.status = "completed" if scope_complete and not value["checks_unfinished"] else "partial"
    run.error_code = None if run.status == "completed" else "inspection_coverage_incomplete"
    run.error_stage = None if run.status == "completed" else "execution"
    run.finished_at = datetime.now(UTC)
    # Explicit resumption uses the original observation, never a silent new scan.
    if run.run_kind != "trial":
        plan.continuation = {}
        if run.status == "completed":
            plan.watermark = run.window_end
    db.commit()


def interrupted(db, run, error_code):
    for result in db.scalars(repository.check_results(run.id)).all():
        if result.execution_status in {"pending", "running"}:
            result.execution_status, result.status = "error", "unknown"
            result.error_code = error_code
            result.summary = "执行中断，检查未完成"
            result.finished_at = datetime.now(UTC)
    db.flush()
    for result in db.scalars(repository.check_results(run.id)).all():
        refresh_object(db, result)
    run.summary = {**run.summary, "coverage": coverage(db, run)}
    if run.status != "cancelled" and run.summary["coverage"]["checks_completed"]:
        run.status = "partial"
