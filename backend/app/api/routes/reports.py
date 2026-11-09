from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response

from app.api.dependencies import Database, require_permission
from app.models import User
from app.schemas.reports import (
    DeliveryResponse,
    EditReport,
    GenerateReport,
    ReportResponse,
    ReviewReport,
    RevisionResponse,
    SendReport,
)
from app.services.reports import management as _service

router = APIRouter(prefix="/api/v1", tags=["postmortems"])

ReadUser = Annotated[User, Depends(require_permission("reports:read"))]

RunUser = Annotated[User, Depends(require_permission("reports:run"))]

ManageUser = Annotated[User, Depends(require_permission("reports:manage"))]

Offset = Annotated[int, Query(ge=0)]

Limit = Annotated[int, Query(ge=1, le=100)]


@router.post("/incidents/{incident_id}/reports", response_model=ReportResponse, status_code=202)
def create(
    incident_id: UUID,
    body: GenerateReport,
    request: Request,
    response: Response,
    db: Database,
    user: RunUser,
):
    return _service.create(incident_id, body, request, response, db, user)


@router.get("/incidents/{incident_id}/reports", response_model=list[ReportResponse])
def history(
    response: Response,
    incident_id: UUID,
    db: Database,
    user: ReadUser,
    offset: Offset = 0,
    limit: Limit = 50,
):
    return _service.history(response, incident_id, db, user, offset, limit)


@router.get("/reports/{identifier}", response_model=ReportResponse)
def get_report(identifier: UUID, db: Database, user: ReadUser):
    return _service.get_report(identifier, db, user)


@router.get("/reports/{identifier}/revisions", response_model=list[RevisionResponse])
def revisions(
    response: Response,
    identifier: UUID,
    db: Database,
    user: ReadUser,
    offset: Offset = 0,
    limit: Limit = 50,
):
    return _service.revisions(response, identifier, db, user, offset, limit)


@router.put("/reports/{identifier}", response_model=ReportResponse)
def update(identifier: UUID, body: EditReport, request: Request, db: Database, user: ManageUser):
    return _service.update(identifier, body, request, db, user)


@router.delete("/reports/{identifier}", status_code=204)
def remove(identifier: UUID, request: Request, db: Database, user: ManageUser):
    return _service.remove(identifier, request, db, user)


@router.post("/reports/{identifier}/review", response_model=ReportResponse)
def review_report(
    identifier: UUID, body: ReviewReport, request: Request, db: Database, user: ManageUser
):
    return _service.review_report(identifier, body, request, db, user)


@router.get("/reports/{identifier}/export")
def export(identifier: UUID, db: Database, user: ReadUser, format: Literal["pdf", "docx", "json"]):
    return _service.export(identifier, db, user, format)


@router.post("/reports/{identifier}/send", response_model=DeliveryResponse, status_code=202)
def send(identifier: UUID, body: SendReport, request: Request, db: Database, user: ManageUser):
    return _service.send(identifier, body, request, db, user)


@router.get("/reports/{identifier}/deliveries", response_model=list[DeliveryResponse])
def deliveries(
    response: Response,
    identifier: UUID,
    db: Database,
    user: ManageUser,
    offset: Offset = 0,
    limit: Limit = 50,
):
    return _service.deliveries(response, identifier, db, user, offset, limit)
