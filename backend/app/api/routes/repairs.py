from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response

from app.api.dependencies import Database, require_permission
from app.models import User
from app.schemas.repairs import (
    HandoffInput,
    HandoffResponse,
    RepairResultInput,
    RepairVerificationInput,
)
from app.services.repairs import management as _service

router = APIRouter(prefix="/api/v1", tags=["repair-handoffs"])

ReadUser = Annotated[User, Depends(require_permission("repairs:read"))]

WriteUser = Annotated[User, Depends(require_permission("repairs:write"))]


@router.post(
    "/incidents/{incident_id}/repair-handoffs", response_model=HandoffResponse, status_code=201
)
def generate(
    incident_id: UUID, body: HandoffInput, db: Database, user: WriteUser, request: Request
):
    return _service.generate(incident_id, body, db, user, request)


@router.get("/incidents/{incident_id}/repair-handoffs", response_model=list[HandoffResponse])
def history(
    incident_id: UUID,
    db: Database,
    user: ReadUser,
    response: Response,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 10,
):
    return _service.history(incident_id, db, user, response, offset, limit)


@router.get("/repair-handoffs/{identifier}", response_model=HandoffResponse)
def detail(identifier: UUID, db: Database, user: ReadUser):
    return _service.detail(identifier, db, user)


@router.post(
    "/repair-handoffs/{identifier}/results", response_model=HandoffResponse, status_code=201
)
def result(
    identifier: UUID, body: RepairResultInput, db: Database, user: WriteUser, request: Request
):
    return _service.result(identifier, body, db, user, request)


@router.post(
    "/repair-handoffs/{identifier}/verifications", response_model=HandoffResponse, status_code=201
)
def verification(
    identifier: UUID, body: RepairVerificationInput, db: Database, user: WriteUser, request: Request
):
    return _service.verification(identifier, body, db, user, request)


@router.get("/repair-handoffs/{identifier}/export")
def export(
    identifier: UUID, db: Database, user: ReadUser, format: Literal["markdown", "json"] = "markdown"
):
    return _service.export(identifier, db, user, format)
