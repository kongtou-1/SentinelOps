"""Model asset export and reviewed import endpoints."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends

from app.api.dependencies import Database, require_permission
from app.models import User
from app.schemas.askdata_stage5 import (
    AskDataImportApplyInput,
    AskDataImportInput,
    AskDataImportPreview,
    AskDataImportResponse,
)
from app.services import askdata_interchange as service

router = APIRouter()
ReadUser = Annotated[User, Depends(require_permission("askdata:read"))]
ManageUser = Annotated[User, Depends(require_permission("askdata:manage"))]


@router.get("/settings/{connection_id}/export")
def export(connection_id: UUID, db: Database, user: ReadUser):
    return service.export_document(db, user, connection_id)


@router.get("/settings/{connection_id}/imports", response_model=list[AskDataImportResponse])
def imports(connection_id: UUID, db: Database, user: ReadUser, limit: int = 20):
    return service.listing(db, user, connection_id, limit)


@router.post(
    "/settings/{connection_id}/imports/preview", response_model=AskDataImportPreview
)
def preview(
    connection_id: UUID, body: AskDataImportInput, db: Database, user: ManageUser
):
    return service.preview(db, user, connection_id, body)


@router.post(
    "/settings/{connection_id}/imports/{identifier}/apply",
    response_model=AskDataImportResponse,
)
def apply(
    connection_id: UUID,
    identifier: UUID,
    body: AskDataImportApplyInput,
    db: Database,
    user: ManageUser,
):
    return service.apply(db, user, connection_id, identifier, body)
