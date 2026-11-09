from datetime import UTC, datetime, timedelta

from fastapi import HTTPException

from app.core.secrets import cipher
from app.models import InspectionItem, InspectionPlan, InspectionReview, InspectionRun
from app.repositories import inspections as repository
from app.repositories.notifications import active_user, audit
from app.schemas.inspections import InspectionPlanResponse
from app.services import service_databases
from app.services.inspections import data_sources, scheduling
from app.services.inspections.sources import validate_database
from app.services.pagination import page
from app.services.records import active_record
from app.utils.redaction import redact


def response(row, application_name=None):
    values = {
        k: getattr(row, k)
        for k in InspectionPlanResponse.model_fields
        if k
        not in {
            "headers_configured",
            "related_sources",
            "application_name",
            "last_run_at",
            "last_status",
            "last_result",
            "last_run_id",
            "group_total",
        }
    }
    import json

    secrets = (
        json.loads(cipher().decrypt(row.encrypted_related_headers.encode()))
        if row.encrypted_related_headers
        else {}
    )
    values["related_sources"] = [
        {**source, "headers_configured": bool(secrets.get(source["name"]))}
        for source in (row.related_sources or [])
    ]
    return InspectionPlanResponse(
        **values,
        headers_configured=bool(row.encrypted_headers),
        application_name=application_name,
    )


def valid_skill(db, plan):
    skill = repository.skill(db, plan.skill_id)
    if not plan.agent_enabled:
        return None
    now = datetime.now(UTC)
    if (
        not skill
        or skill.deleted_at
        or not skill.enabled
        or (skill.expires_at and skill.expires_at <= now)
        or (skill.application_ids and str(plan.application_id) not in skill.application_ids)
        or skill.applicable_versions
    ):
        raise HTTPException(409, "inspection_skill_unavailable")
    return skill


def validate_plan(db, plan):
    if plan.execution_mode == "agent":
        from app.services.inspections.workflow import authorized_sources

        try:
            authorized_sources(db, plan)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
    elif plan.execution_mode != "checks" and (
        plan.source_config.get("path_template")
        or plan.source_config.get("request_method", "GET") != "GET"
    ):
        raise HTTPException(422, "动态路径及 POST 接口仅适用于 Agent 自主巡检")
    if plan.data_source_id:
        source = data_sources.active(db, plan.data_source_id)
        if (
            not source.enabled
            or source.environment != plan.environment
            or (source.application_id and source.application_id != plan.application_id)
        ):
            raise HTTPException(409, "inspection_source_scope_unavailable")
    if plan.application_id:
        app = repository.application(db, plan.application_id)
        if not app or app.deleted_at or not app.enabled:
            raise HTTPException(409, "inspection_service_unavailable")
    user = active_user(db, plan.configured_by)
    if not user or user.role.code != "admin":
        raise HTTPException(409, "inspection_authorization_revoked")
    skill = valid_skill(db, plan)
    if plan.execution_mode == "checks":
        from app.services.inspections import checks

        checks.configuration(db, plan)
        if (
            plan.object_mode == "dynamic"
            and plan.source_kind == "api"
            and (
                plan.source_config.get("request_method", "GET") != "GET"
                or "{" in plan.source_config.get("path_template", "")
            )
        ):
            raise HTTPException(422, "inspection_discovery_requires_fixed_get")
    if skill and plan.skill_version != skill.version:
        raise HTTPException(409, "inspection_skill_version_changed")
    if plan.source_kind == "database":
        validate_database(db, plan)
    from app.services.inspections.sources import related_view

    for source in plan.related_sources or []:
        if source["source_kind"] == "database":
            validate_database(db, related_view(plan, source))
    return skill


def listing(
    db, response_header, application_id, offset, limit, search=None, enabled=None, environment=None
):
    result = page(
        db,
        repository.plans(application_id, search, enabled, environment),
        response_header,
        offset,
        limit,
    )
    names = repository.application_names(
        db, {row.application_id for row in result if row.application_id}
    )
    latest = repository.latest_runs(db, [row.id for row in result])
    counts = repository.health_counts(db, [run.id for run in latest.values()])
    group_counts = repository.plan_group_counts(db, application_id, search, enabled, environment)
    values = []
    for row in result:
        value = response(row, names.get(row.application_id))
        value.group_total = group_counts.get((row.application_id, row.environment), 0)
        run = latest.get(row.id)
        if run:
            value.last_run_id = run.id
            value.last_run_at, value.last_status = run.started_at or run.created_at, run.status
            health = counts.get(run.id, {})
            value.last_result = (
                "fail"
                if health.get("fail")
                else "unknown"
                if health.get("unknown") or not health
                else "pass"
            )
        values.append(value)
        if run and run.snapshot.get("checks"):
            from app.services.inspections.checks_runtime import coverage

            value.last_result = coverage(db, run)["conclusion"]
    return values


def configure(db, body, user, identifier=None, *, trial_only=False, commit=True):
    import json

    row = repository.plan(db, identifier, lock=True) if identifier else InspectionPlan()
    if identifier and not row:
        raise HTTPException(404, "Inspection plan not found")
    if identifier and row.trial_only:
        raise HTTPException(409, "inspection_trial_snapshot_readonly")
    if identifier and body.expected_version != row.version:
        raise HTTPException(409, "inspection_plan_version_changed")
    if identifier:
        cancel_active(db, row.id, "plan_changed")
    old_urls = {s["name"]: s["source_config"].get("url") for s in (row.related_sources or [])}
    primary_url_changed = identifier and row.source_config.get("url") != body.source_config.url
    old_headers = (
        json.loads(cipher().decrypt(row.encrypted_related_headers.encode()))
        if row.encrypted_related_headers
        else {}
    )
    related_headers = {}
    for source in body.related_sources:
        if source.source_kind == "api":
            related_headers[source.name] = (
                {k: v.get_secret_value() for k, v in source.headers.items()}
                if source.headers is not None
                else old_headers.get(source.name, {})
                if old_urls.get(source.name) == source.source_config.url
                else {}
            )
    values = body.model_dump(
        mode="json", exclude={"headers", "expected_version", "related_sources"}
    )
    values["related_sources"] = [
        source.model_dump(mode="json", exclude={"headers"}) for source in body.related_sources
    ]
    for key, value in values.items():
        setattr(row, key, value)
    # Pydantic JSON serializes UUIDs; mapped UUID columns retain native values.
    row.application_id, row.skill_id, row.data_source_id = (
        body.application_id,
        body.skill_id,
        body.data_source_id,
    )
    row.encrypted_related_headers = (
        cipher().encrypt(json.dumps(related_headers).encode()).decode()
        if any(related_headers.values())
        else None
    )
    row.configured_by = user.id
    row.trial_only = trial_only
    row.version = row.version + 1 if identifier else 1
    row.next_run_at = scheduling.next_run(row, initial=True) if row.enabled else None
    row.watermark, row.continuation = None, {}
    if body.headers is not None:
        headers = {k: v.get_secret_value() for k, v in body.headers.items()}
        row.encrypted_headers = (
            cipher().encrypt(json.dumps(headers).encode()).decode() if headers else None
        )
    if row.source_kind != "api":
        row.encrypted_headers = None
    elif primary_url_changed and body.headers is None:
        row.encrypted_headers = None
    data_sources.apply_to_plan(db, row)
    data_sources.apply_related(db, row)
    skill = valid_skill(db, row)
    row.skill_version = skill.version if skill else None
    # Validate source scope even for paused plans; no business records are read here.
    validate_plan(db, row)
    db.add(row)
    db.flush()
    audit(
        db,
        user,
        "inspection_trial_snapshot_created" if trial_only else "inspection_plan_configured",
        "inspection_plan",
        row.id,
        {"version": row.version, "enabled": row.enabled},
    )
    if commit:
        db.commit()
    db.refresh(row)
    return response(row)


def cancel_active(db, plan_id, reason):
    run = repository.active_run(db, plan_id)
    if run:
        run.status, run.error_code = "cancelled", reason
        run.finished_at = datetime.now(UTC)


def set_state(db, identifier, body, user):
    row = repository.plan(db, identifier, lock=True)
    if not row:
        raise HTTPException(404, "Inspection plan not found")
    if row.trial_only:
        raise HTTPException(409, "inspection_trial_snapshot_readonly")
    if row.version != body.expected_version:
        raise HTTPException(409, "inspection_plan_version_changed")
    if body.enabled:
        row.configured_by = user.id
        validate_plan(db, row)
    run = repository.active_run(db, row.id)
    row.enabled = body.enabled
    row.version += 1
    if run:
        run.plan_version = row.version
    row.next_run_at = scheduling.next_run(row, initial=True) if row.enabled else None
    audit(
        db,
        user,
        "inspection_plan_state_changed",
        "inspection_plan",
        row.id,
        {"enabled": row.enabled, "version": row.version},
    )
    db.commit()
    db.refresh(row)
    return response(row)


def remove(db, identifier, version, user):
    row = repository.plan(db, identifier, lock=True)
    if not row:
        raise HTTPException(404, "Inspection plan not found")
    if row.version != version:
        raise HTTPException(409, "inspection_plan_version_changed")
    cancel_active(db, row.id, "plan_deleted")
    row.enabled = False
    row.version += 1
    row.deleted_at = row.updated_at = datetime.now(UTC)
    audit(db, user, "inspection_plan_deleted", "inspection_plan", row.id)
    db.commit()


def enqueue(db, plan, user, trigger="manual", now=None):
    now = now or datetime.now(UTC)
    if (
        not plan.enabled
        and trigger != "manual"
        and not (trigger == "trial" and plan.execution_mode == "checks")
    ):
        raise HTTPException(409, "inspection_plan_paused")
    if plan.trial_only and trigger != "trial":
        raise HTTPException(409, "inspection_trial_snapshot_readonly")
    data_source = data_sources.apply_to_plan(db, plan)
    related_versions = data_sources.apply_related(db, plan)
    skill = validate_plan(db, plan)
    existing = repository.active_run(db, plan.id)
    if existing:
        return existing
    pending = plan.continuation
    start = (
        datetime.fromisoformat(pending["start"])
        if pending
        else (
            plan.watermark - timedelta(seconds=300)
            if plan.watermark and not plan.related_sources
            else now - timedelta(seconds=plan.lookback_seconds)
        )
    )
    end = datetime.fromisoformat(pending["end"]) if pending else now
    snapshot = {
        "related_source_versions": related_versions,
        "data_source_version": data_source.version if data_source else None,
        "plan": response(plan).model_dump(mode="json"),
        "skill": {
            "id": str(skill.id),
            "version": skill.version,
            "content": skill.content,
            "scripts": skill.scripts,
        }
        if skill
        else None,
    }
    if plan.execution_mode == "checks":
        from app.services.inspections import checks

        snapshot["checks"] = checks.configuration(db, plan)
    if plan.execution_mode == "agent":
        from app.services.inspections.workflow import authorized_sources

        snapshot["agent_source_versions"] = {
            str(source.id): source.version for source in authorized_sources(db, plan)
        }
    if plan.source_kind == "database":
        snapshot["database"] = service_databases.frozen(validate_database(db, plan))
    from app.services.inspections.sources import related_view

    snapshot["related_databases"] = {
        source["name"]: service_databases.frozen(validate_database(db, related_view(plan, source)))
        for source in (plan.related_sources or [])
        if source["source_kind"] == "database"
    }
    row = InspectionRun(
        plan_id=plan.id,
        plan_version=plan.version,
        trigger=trigger,
        run_kind="trial" if trigger == "trial" else "standard",
        requested_by=user.id,
        status="queued",
        snapshot=redact(snapshot),
        window_start=start,
        window_end=end,
        page_cursor=pending.get("cursor", {}) if pending else {},
    )
    db.add(row)
    if trigger == "automatic":
        plan.next_run_at = scheduling.next_run(plan, now)
    db.flush()
    audit(db, user, "inspection_enqueued", "inspection_run", row.id, {"trigger": trigger})
    return row


def schedule_due(db, now=None):
    now = now or datetime.now(UTC)
    for plan in repository.due(db, now):
        try:
            enqueue(db, plan, active_user(db, plan.configured_by), "automatic", now)
        except HTTPException as exc:
            plan.enabled = False
            cancel_active(db, plan.id, str(exc.detail))
            audit(
                db,
                None,
                "inspection_suspended",
                "inspection_plan",
                plan.id,
                {"reason": str(exc.detail)},
            )
    db.commit()


def run_now(db, identifier, user):
    plan = repository.plan(db, identifier, lock=True)
    if not plan:
        raise HTTPException(404, "Inspection plan not found")
    row = enqueue(db, plan, user)
    db.commit()
    db.refresh(row)
    return row


def trigger(db, identifier, body, user):
    plan = repository.plan(db, identifier, lock=True)
    if not plan:
        raise HTTPException(404, "Inspection plan not found")
    if body.kind == "event" and plan.schedule_kind != "event":
        raise HTTPException(409, "计划未配置事件触发")
    row = enqueue(db, plan, user, body.kind)
    db.commit()
    db.refresh(row)
    return row


def cancel(db, identifier, user):
    run = active_record(db, InspectionRun, identifier)
    plan = repository.plan(db, run.plan_id, lock=True)
    if not plan:
        raise HTTPException(409, "inspection_plan_deleted")
    db.refresh(run)
    if run.status in repository.ACTIVE:
        run.status, run.error_code = "cancelled", "user_cancelled"
        run.finished_at = datetime.now(UTC)
        if run.snapshot.get("checks"):
            from app.services.inspections import checks_runtime

            checks_runtime.interrupted(db, run, "user_cancelled")
            run.status = "cancelled"
        audit(db, user, "inspection_cancelled", "inspection_run", run.id)
        db.commit()
    return run


def remove_run(db, identifier, user):
    run = repository.run(db, identifier, lock=True)
    if not run:
        raise HTTPException(404, "Inspection run not found")
    if run.status in repository.ACTIVE:
        raise HTTPException(409, "请先停止本轮巡检，再删除运行记录")
    now = datetime.now(UTC)
    count = repository.delete_run_items(db, run.id, now)
    run.deleted_at = run.updated_at = now
    audit(db, user, "inspection_run_deleted", "inspection_run", run.id, {"items": count})
    db.commit()


def remove_item(db, identifier, user):
    item = active_record(db, InspectionItem, identifier)
    run = repository.run(db, item.run_id, lock=True)
    if not run:
        raise HTTPException(404, "Inspection run not found")
    if run.status in repository.ACTIVE:
        raise HTTPException(409, "请先停止本轮巡检，再删除巡检结果")
    item = active_record(db, InspectionItem, identifier)
    item.deleted_at = item.updated_at = datetime.now(UTC)
    audit(db, user, "inspection_item_deleted", "inspection_item", item.id)
    db.commit()


def review(db, identifier, body, user):
    active_record(db, InspectionItem, identifier)
    row = InspectionReview(item_id=identifier, reviewer_id=user.id, **redact(body.model_dump()))
    db.add(row)
    audit(db, user, "inspection_reviewed", "inspection_item", identifier, {"verdict": body.verdict})
    db.commit()
    db.refresh(row)
    return row
