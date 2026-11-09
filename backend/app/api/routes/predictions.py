from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Response
from pydantic import AwareDatetime

from app.api.dependencies import Database, require_permission
from app.models import User
from app.schemas.predictions import ResultResponse, TaskInput, TaskResponse
from app.services.predictions import management as _service

router = APIRouter(prefix="/api/v1", tags=["predictions"])

ReadUser = Annotated[User, Depends(require_permission("predictions:read"))]

ManageUser = Annotated[User, Depends(require_permission("predictions:manage"))]

RunUser = Annotated[User, Depends(require_permission("predictions:run"))]


@router.get("/prediction-tasks", response_model=list[TaskResponse])
def tasks(
    response: Response,
    db: Database,
    user: ReadUser,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
):
    return _service.tasks(response, db, user, offset, limit)


@router.post("/prediction-tasks", response_model=TaskResponse, status_code=201)
def create_task(body: TaskInput, db: Database, user: ManageUser):
    return _service.create_task(body, db, user)


@router.get("/prediction-tasks/{task_id}", response_model=TaskResponse)
def task_detail(task_id: UUID, db: Database, user: ReadUser):
    return _service.task_detail(task_id, db, user)


@router.put("/prediction-tasks/{task_id}", response_model=TaskResponse)
def replace_task(task_id: UUID, body: TaskInput, db: Database, user: ManageUser):
    return _service.replace_task(task_id, body, db, user)


@router.delete("/prediction-tasks/{task_id}", status_code=204)
def delete_task(task_id: UUID, db: Database, user: ManageUser):
    return _service.delete_task(task_id, db, user)


@router.post("/prediction-tasks/{task_id}/run", response_model=ResultResponse, status_code=202)
def run_task(task_id: UUID, db: Database, user: RunUser):
    return _service.run_task(task_id, db, user)


@router.get("/predictions", response_model=list[ResultResponse])
def results(
    response: Response,
    db: Database,
    user: ReadUser,
    task_id: UUID | None = None,
    server_id: UUID | None = None,
    container_id: UUID | None = None,
    llm_endpoint_id: UUID | None = None,
    status: Literal["queued", "running", "completed", "failed", "interrupted"] | None = None,
    start: AwareDatetime | None = None,
    end: AwareDatetime | None = None,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
):
    return _service.results(
        response,
        db,
        user,
        task_id,
        server_id,
        container_id,
        llm_endpoint_id,
        status,
        start,
        end,
        offset,
        limit,
    )


@router.get("/predictions/{result_id}", response_model=ResultResponse)
def result_detail(result_id: UUID, db: Database, user: ReadUser):
    return _service.result_detail(result_id, db, user)


@router.get("/monitoring/prediction-metrics")
def metrics(db: Database, authorization: Annotated[str | None, Header()] = None):
    return _service.metrics(db, authorization)
