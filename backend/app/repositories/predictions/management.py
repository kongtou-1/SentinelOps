"""Database access; transaction ownership stays with the caller."""

from sqlalchemy import func, select

from app.models import PredictionResult, PredictionTask


def result_response_query(db, result):
    task = db.get(PredictionTask, result.task_id)
    return task


def tasks_query():
    return (
        select(PredictionTask)
        .where(PredictionTask.deleted_at.is_(None))
        .order_by(PredictionTask.name)
    )


def replace_task_query(db, task):
    db.refresh(task, with_for_update=True)


def replace_task_query_2():
    return func.clock_timestamp()


def delete_task_query(db, task):
    db.refresh(task, with_for_update=True)


def delete_task_query_2():
    return func.clock_timestamp()


def results_query():
    query = (
        select(PredictionResult)
        .join(PredictionTask)
        .where(PredictionResult.deleted_at.is_(None), PredictionTask.deleted_at.is_(None))
    )
    return query


def results_query_2(key, query, value):
    query = query.where(getattr(PredictionTask, key) == value)
    return query


def results_query_3(query, status):
    query = query.where(PredictionResult.status == status)
    return query


def results_query_4(end, query, start):
    query = query.where(PredictionResult.created_at >= start, PredictionResult.created_at <= end)
    return query


def results_query_5(query):
    query = query.order_by(PredictionResult.created_at.desc())
    return query
