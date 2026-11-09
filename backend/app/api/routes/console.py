"""Read-only projections for the web console; authorization remains in existing actions."""

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response
from pydantic import AwareDatetime

from app.api.dependencies import Database, require_permission
from app.models import (
    User,
)
from app.schemas.console import ConsoleItem, DashboardSummary
from app.services import console as _service

router = APIRouter(prefix="/api/v1", tags=["console"])

ReadExecution = Annotated[User, Depends(require_permission("executions:read"))]

ReadReport = Annotated[User, Depends(require_permission("reports:read"))]

ReadDashboard = Annotated[User, Depends(require_permission("resources:read"))]

Offset = Annotated[int, Query(ge=0)]

Limit = Annotated[int, Query(ge=1, le=100)]


@router.get("/approvals", response_model=list[ConsoleItem])
def approvals(
    db: Database,
    response: Response,
    user: ReadExecution,
    status: Literal["pending", "approved", "rejected", "consumed", "expired", "invalidated"]
    | None = None,
    incident_id: UUID | None = None,
    actor_id: UUID | None = None,
    start: AwareDatetime | None = None,
    end: AwareDatetime | None = None,
    offset: Offset = 0,
    limit: Limit = 50,
):
    return _service.approvals(
        db, response, user, status, incident_id, actor_id, start, end, offset, limit
    )


@router.get("/executions", response_model=list[ConsoleItem])
def executions(
    db: Database,
    response: Response,
    user: ReadExecution,
    status: Literal[
        "queued",
        "prechecking",
        "executing",
        "verifying",
        "succeeded",
        "failed",
        "interrupted",
        "unknown",
    ]
    | None = None,
    incident_id: UUID | None = None,
    actor_id: UUID | None = None,
    start: AwareDatetime | None = None,
    end: AwareDatetime | None = None,
    offset: Offset = 0,
    limit: Limit = 50,
):
    return _service.executions(
        db, response, user, status, incident_id, actor_id, start, end, offset, limit
    )


@router.get("/reports", response_model=list[ConsoleItem])
def reports(
    db: Database,
    response: Response,
    user: ReadReport,
    status: Literal["queued", "running", "draft", "approved", "failed", "interrupted"]
    | None = None,
    incident_id: UUID | None = None,
    actor_id: UUID | None = None,
    start: AwareDatetime | None = None,
    end: AwareDatetime | None = None,
    offset: Offset = 0,
    limit: Limit = 50,
):
    return _service.reports(
        db, response, user, status, incident_id, actor_id, start, end, offset, limit
    )


@router.get("/dashboard/summary", response_model=DashboardSummary)
def summary(db: Database, user: ReadDashboard):
    return _service.summary(db, user)
