from datetime import timedelta

from fastapi import HTTPException, Response

import app.repositories.predictions.management as _repository
from app.core.config import get_settings
from app.core.service_auth import service_authorization
from app.models import PredictionResult, PredictionTask
from app.schemas.predictions import ResultResponse, TaskResponse
from app.services.pagination import page
from app.services.predictions.tasks import (
    assert_idle,
    enqueue,
    export_metrics,
    is_fresh,
    latest,
    source,
)
from app.services.records import active_record, save


def task_response(db, task):
    response = TaskResponse.model_validate(task)
    run = latest(db, task.id)
    if run:
        response.latest_result = {
            "id": str(run.id),
            "status": run.status,
            "error_code": run.error_code,
            "fresh": is_fresh(db, run, task),
        }
    return response


def result_response(db, result):
    response = ResultResponse.model_validate(result)
    task = _repository.result_response_query(db, result)
    response.fresh = not task.deleted_at and is_fresh(db, result, task)
    return response


def tasks(response, db, user, offset=0, limit=50):
    return [
        task_response(db, task)
        for task in page(
            db,
            _repository.tasks_query(),
            response,
            offset,
            limit,
        )
    ]


def create_task(body, db, user):
    task = PredictionTask(**body.model_dump())
    source(db, task)
    return task_response(db, save(db, task))


def task_detail(task_id, db, user):
    return task_response(db, active_record(db, PredictionTask, task_id))


def replace_task(task_id, body, db, user):
    task = active_record(db, PredictionTask, task_id)
    _repository.replace_task_query(db, task)
    assert_idle(db, task_id)
    source(db, PredictionTask(**body.model_dump()))
    for key, value in body.model_dump().items():
        setattr(task, key, value)
    task.next_run_at = _repository.replace_task_query_2()
    return task_response(db, save(db, task))


def delete_task(task_id, db, user):
    task = active_record(db, PredictionTask, task_id)
    _repository.delete_task_query(db, task)
    assert_idle(db, task_id)
    task.deleted_at = _repository.delete_task_query_2()
    db.commit()


def run_task(task_id, db, user):
    return result_response(db, enqueue(db, active_record(db, PredictionTask, task_id)))


def results(
    response,
    db,
    user,
    task_id=None,
    server_id=None,
    container_id=None,
    llm_endpoint_id=None,
    status=None,
    start=None,
    end=None,
    offset=0,
    limit=50,
):
    if (start is None) != (end is None):
        raise HTTPException(422, "Provide both start and end")
    if start is not None and (end <= start or end - start > timedelta(days=31)):
        raise HTTPException(422, "Invalid time range")
    query = _repository.results_query()
    for key, value in (
        ("id", task_id),
        ("server_id", server_id),
        ("container_id", container_id),
        ("llm_endpoint_id", llm_endpoint_id),
    ):
        if value:
            query = _repository.results_query_2(key, query, value)
    if status:
        query = _repository.results_query_3(query, status)
    if start is not None:
        query = _repository.results_query_4(end, query, start)
    query = _repository.results_query_5(query)
    return [result_response(db, run) for run in page(db, query, response, offset, limit)]


def result_detail(result_id, db, user):
    result = active_record(db, PredictionResult, result_id)
    active_record(db, PredictionTask, result.task_id)
    return result_response(db, result)


def metrics(db, authorization=None):
    service_authorization(authorization, get_settings().monitoring_token_file)
    return Response(export_metrics(db), media_type="text/plain; version=0.0.4")
