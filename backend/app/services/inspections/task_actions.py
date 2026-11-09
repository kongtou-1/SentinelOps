"""Task actions share the same configuration and execution validation as normal runs."""

from datetime import UTC, datetime

from fastapi import HTTPException

from app.repositories import inspections as repository
from app.repositories.notifications import audit
from app.schemas.inspections import InspectionPlanInput
from app.services.access_control import require
from app.services.inspections import plans, scheduling


def trial(db, body, user):
    require(db, user, "inspections:run")
    values = body.plan.model_dump(mode="python")
    values.update(
        enabled=False,
        schedule_kind="manual",
        expected_version=None,
        max_records=min(body.sample_size, body.plan.max_records),
        max_retries=0,
    )
    definition = InspectionPlanInput.model_validate(values)
    # The hidden plan owns this run's scope and encrypted credential references. It never
    # appears among saved tasks or enters scheduling. The form and saved task stay untouched.
    value = plans.configure(db, definition, user, trial_only=True, commit=False)
    plan = repository.plan(db, value.id)
    row = plans.enqueue(db, plan, user, "trial")
    row.snapshot = {
        **row.snapshot,
        "trial": {"sample_limit": definition.max_records, "full_scope": False},
    }
    db.commit()
    db.refresh(row)
    return row


def copy(db, identifier, user):
    source = repository.plan(db, identifier, lock=True)
    if not source or source.trial_only:
        raise HTTPException(404, "Inspection plan not found")
    value = plans.response(source).model_dump(mode="python")
    values = {key: value[key] for key in InspectionPlanInput.model_fields if key in value}
    values.update(name=f"{source.name[:95]}（副本）", enabled=False)
    values["related_sources"] = [
        {key: value for key, value in item.items() if key != "headers_configured"}
        for item in values["related_sources"]
    ]
    result = plans.configure(db, InspectionPlanInput.model_validate(values), user, commit=False)
    row = repository.plan(db, result.id)
    # Reuse encrypted configuration without ever passing plaintext through the response.
    row.encrypted_headers = source.encrypted_headers
    row.encrypted_related_headers = source.encrypted_related_headers
    row.next_run_at = None
    audit(
        db, user, "inspection_plan_copied", "inspection_plan", row.id, {"source_id": str(source.id)}
    )
    db.commit()
    db.refresh(row)
    return plans.response(row)


def schedule_preview(body):
    try:
        from zoneinfo import ZoneInfo

        ZoneInfo(body.schedule_timezone)
        now = datetime.now(UTC)
        if body.schedule_kind == "once" and (body.scheduled_at is None or body.scheduled_at <= now):
            raise ValueError("Specify a future execution time")
        times = []
        for index in range(3):
            value = scheduling.next_run(body, now, initial=index == 0)
            if value is None or value < now:
                break
            times.append(value)
            now = value
        return {"times": times}
    except (ValueError, KeyError):
        raise HTTPException(422, "巡检执行时间或时区无效") from None
