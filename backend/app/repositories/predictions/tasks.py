"""Database access; transaction ownership stays with the caller."""

from sqlalchemy import select

from app.models import Container, PredictionResult, PredictionTask


def snapshot_query(db, task):
    return db.get(Container, task.container_id)


def assert_idle_query(ACTIVE, db, task_id):
    return db.scalar(
        select(PredictionResult.id).where(
            PredictionResult.task_id == task_id,
            PredictionResult.deleted_at.is_(None),
            PredictionResult.status.in_(ACTIVE),
        )
    )


def enqueue_query(db, task):
    db.refresh(task, with_for_update=True)


def latest_query(task_id):
    query = select(PredictionResult).where(
        PredictionResult.task_id == task_id, PredictionResult.deleted_at.is_(None)
    )
    return query


def latest_query_2(query):
    query = query.where(PredictionResult.status == "completed")
    return query


def latest_query_3(db, query):
    return db.scalar(
        query.order_by(PredictionResult.created_at.desc(), PredictionResult.id.desc()).limit(1)
    )


def schedule_due_query(db, now):
    ids = list(
        db.scalars(
            select(PredictionTask.id)
            .where(
                PredictionTask.enabled.is_(True),
                PredictionTask.deleted_at.is_(None),
                PredictionTask.next_run_at <= now,
            )
            .order_by(PredictionTask.next_run_at)
            .limit(100)
        )
    )
    return ids


def schedule_due_query_2(db, task_id):
    task = db.scalar(
        select(PredictionTask).where(PredictionTask.id == task_id).with_for_update(skip_locked=True)
    )
    return task


def claim_query(db):
    result = db.scalar(
        select(PredictionResult)
        .where(PredictionResult.status == "queued", PredictionResult.deleted_at.is_(None))
        .order_by(PredictionResult.created_at)
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    return result


def interrupt_runs_query(db):
    return db.scalars(select(PredictionResult).where(PredictionResult.status == "running"))


def complete_query(db, result_id):
    result = db.get(PredictionResult, result_id)
    return result


def complete_query_2(db, result):
    task = db.get(PredictionTask, result.task_id)
    return task


def export_metrics_query(db):
    return db.scalars(
        select(PredictionTask).where(
            PredictionTask.enabled.is_(True), PredictionTask.deleted_at.is_(None)
        )
    )
