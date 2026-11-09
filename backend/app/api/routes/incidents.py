from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import AwareDatetime

from app.api.dependencies import Database, require_permission
from app.models import (
    User,
)
from app.schemas.incidents import (
    AssigneeResponse,
    CommentInput,
    IncidentDetail,
    IncidentResponse,
    IncidentStatus,
    IncidentUpdate,
    MergeInput,
    StatusInput,
    TimelineResponse,
)
from app.schemas.resources import AlertResponse
from app.services.incidents import management as _service

router = APIRouter(prefix="/api/v1/incidents", tags=["incidents"])

ReadUser = Annotated[User, Depends(require_permission("incidents:read"))]

HandleUser = Annotated[User, Depends(require_permission("incidents:handle"))]

Offset = Annotated[int, Query(ge=0)]

Limit = Annotated[int, Query(ge=1, le=100)]


@router.get("", response_model=list[IncidentResponse])
def incidents(
    response: Response,
    db: Database,
    user: ReadUser,
    status: IncidentStatus | None = None,
    severity: Annotated[str | None, Query(pattern="^(warning|critical)$")] = None,
    assignee_id: UUID | None = None,
    server_id: UUID | None = None,
    llm_endpoint_id: UUID | None = None,
    start: AwareDatetime | None = None,
    end: AwareDatetime | None = None,
    unassigned: bool = False,
    offset: Offset = 0,
    limit: Limit = 50,
):
    return _service.incidents(
        response,
        db,
        user,
        status,
        severity,
        assignee_id,
        server_id,
        llm_endpoint_id,
        start,
        end,
        unassigned,
        offset,
        limit,
    )


@router.get("/assignees", response_model=list[AssigneeResponse])
def assignees(
    response: Response, db: Database, user: ReadUser, offset: Offset = 0, limit: Limit = 50
):
    return _service.assignees(response, db, user, offset, limit)


@router.get("/{incident_id}", response_model=IncidentDetail)
def detail(incident_id: UUID, db: Database, user: ReadUser):
    return _service.detail(incident_id, db, user)


@router.get("/{incident_id}/alerts", response_model=list[AlertResponse])
def alerts(
    response: Response,
    incident_id: UUID,
    db: Database,
    user: ReadUser,
    offset: Offset = 0,
    limit: Limit = 50,
):
    return _service.alerts(response, incident_id, db, user, offset, limit)


@router.get("/{incident_id}/timeline", response_model=list[TimelineResponse])
def timeline(
    response: Response,
    incident_id: UUID,
    db: Database,
    user: ReadUser,
    offset: Offset = 0,
    limit: Limit = 50,
):
    return _service.timeline(response, incident_id, db, user, offset, limit)


@router.patch("/{incident_id}", response_model=IncidentResponse)
def update(incident_id: UUID, body: IncidentUpdate, db: Database, user: HandleUser):
    return _service.update(incident_id, body, db, user)


@router.post("/{incident_id}/status", response_model=IncidentResponse)
def status(incident_id: UUID, body: StatusInput, request: Request, db: Database, user: HandleUser):
    return _service.status(incident_id, body, request, db, user)


@router.post("/{incident_id}/comments", response_model=TimelineResponse, status_code=201)
def comment(incident_id: UUID, body: CommentInput, db: Database, user: HandleUser):
    return _service.comment(incident_id, body, db, user)


@router.post("/{incident_id}/merge", response_model=IncidentResponse)
def merge(incident_id: UUID, body: MergeInput, db: Database, user: HandleUser):
    return _service.merge(incident_id, body, db, user)
