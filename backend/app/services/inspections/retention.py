"""Preview or soft-delete expired trial runs. Formal records remain manually managed."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update

from app.models import (
    InspectionCheckResult,
    InspectionItem,
    InspectionReview,
    InspectionRun,
    InspectionTrace,
)
from app.repositories import inspections as repository
from app.repositories.notifications import audit


def clean_trials(db, days=7, apply=False, now=None):
    if days < 7:
        raise ValueError("Trial retention must be at least seven days")
    now = now or datetime.now(UTC)
    reviewed = select(InspectionReview.item_id).where(InspectionReview.deleted_at.is_(None))
    protected = select(InspectionItem.run_id).where(InspectionItem.id.in_(reviewed))
    candidates = db.scalars(
        select(InspectionRun)
        .where(
            InspectionRun.run_kind == "trial",
            InspectionRun.deleted_at.is_(None),
            InspectionRun.finished_at < now - timedelta(days=days),
            InspectionRun.status.not_in(repository.ACTIVE),
            InspectionRun.id.not_in(protected),
        )
        .order_by(InspectionRun.finished_at)
        .limit(100)
    ).all()
    if apply:
        for run in candidates:
            plan = repository.plan(db, run.plan_id, lock=True)
            if not plan or not plan.trial_only:
                continue
            db.refresh(run)
            if run.status in repository.ACTIVE or run.deleted_at:
                continue
            for model in (InspectionTrace, InspectionCheckResult, InspectionItem):
                db.execute(
                    update(model)
                    .where(model.run_id == run.id, model.deleted_at.is_(None))
                    .values(deleted_at=now, updated_at=now)
                )
            run.deleted_at = run.updated_at = now
            plan.deleted_at = plan.updated_at = now
            audit(
                db,
                None,
                "inspection_trial_expired",
                "inspection_run",
                run.id,
                {"retention_days": days},
            )
        db.commit()
    return [str(run.id) for run in candidates]
