from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Body, Depends, Query, Request, Response
from pydantic import AwareDatetime

from app.api.dependencies import Database, require_permission
from app.models import User
from app.schemas.executions import (
    ApprovalInput,
    ApprovalResponse,
    AuditResponse,
    DecisionInput,
    ExecuteInput,
    ExecutionResponse,
)
from app.services.executions import approvals as _service

router = APIRouter(prefix="/api/v1", tags=["approvals and executions"])

ReadUser = Annotated[User, Depends(require_permission("executions:read"))]

RunUser = Annotated[User, Depends(require_permission("executions:run"))]

ApproveUser = Annotated[User, Depends(require_permission("approvals:decide"))]

AuditUser = Annotated[User, Depends(require_permission("audit:read"))]

Offset = Annotated[int, Query(ge=0)]

Limit = Annotated[int, Query(ge=1, le=100)]


@router.get("/action-skills")
def skills(user: ReadUser):
    return _service.skills(user)


@router.post("/incidents/{incident_id}/approvals", response_model=ApprovalResponse, status_code=201)
def request_approval(
    incident_id: UUID, body: ApprovalInput, request: Request, db: Database, user: RunUser
):
    return _service.request_approval(incident_id, body, request, db, user)


@router.get("/incidents/{incident_id}/approvals", response_model=list[ApprovalResponse])
def approvals(
    response: Response,
    incident_id: UUID,
    db: Database,
    user: ReadUser,
    offset: Offset = 0,
    limit: Limit = 50,
):
    return _service.approvals(response, incident_id, db, user, offset, limit)


@router.get("/approvals/{identifier}", response_model=ApprovalResponse)
def approval(identifier: UUID, db: Database, user: ReadUser):
    return _service.approval(identifier, db, user)


@router.post("/approvals/{identifier}/approve", response_model=ApprovalResponse)
def approve(
    identifier: UUID, body: DecisionInput, request: Request, db: Database, user: ApproveUser
):
    return _service.approve(identifier, body, request, db, user)


@router.post("/approvals/{identifier}/reject", response_model=ApprovalResponse)
def reject(
    identifier: UUID, body: DecisionInput, request: Request, db: Database, user: ApproveUser
):
    return _service.reject(identifier, body, request, db, user)


@router.post("/approvals/{identifier}/execute", response_model=ExecutionResponse, status_code=202)
def execute(
    identifier: UUID,
    request: Request,
    db: Database,
    user: RunUser,
    body: Annotated[ExecuteInput | None, Body()] = None,
):
    return _service.execute(identifier, request, db, user, body)


@router.get("/incidents/{incident_id}/executions", response_model=list[ExecutionResponse])
def executions(
    response: Response,
    incident_id: UUID,
    db: Database,
    user: ReadUser,
    offset: Offset = 0,
    limit: Limit = 50,
):
    return _service.executions(response, incident_id, db, user, offset, limit)


@router.get("/executions/{identifier}", response_model=ExecutionResponse)
def execution(identifier: UUID, db: Database, user: ReadUser):
    return _service.execution(identifier, db, user)


@router.get("/audit-logs", response_model=list[AuditResponse])
def audits(
    response: Response,
    db: Database,
    user: AuditUser,
    incident_id: UUID | None = None,
    approval_id: UUID | None = None,
    execution_id: UUID | None = None,
    report_id: UUID | None = None,
    user_id: UUID | None = None,
    action: Annotated[str | None, Query(max_length=100)] = None,
    start: AwareDatetime | None = None,
    end: AwareDatetime | None = None,
    offset: Offset = 0,
    limit: Limit = 50,
    q: Annotated[str | None, Query(max_length=200)] = None,
):
    return _service.audits(
        response,
        db,
        user,
        incident_id,
        approval_id,
        execution_id,
        report_id,
        user_id,
        action,
        start,
        end,
        offset,
        limit,
        q,
    )
