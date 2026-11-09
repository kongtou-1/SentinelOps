"""Version lifecycle endpoints."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends

from app.api.dependencies import Database, require_permission
from app.models import User
from app.schemas.askdata import (
    AskDataConfigurationResponse,
    AskDataVersionInput,
    AskDataVersionResponse,
)
from app.services import askdata_versions as service

router = APIRouter()
ReadUser = Annotated[User, Depends(require_permission("askdata:read"))]
ManageUser = Annotated[User, Depends(require_permission("askdata:manage"))]


@router.get("/connections/{identifier}/versions", response_model=list[AskDataVersionResponse])
def versions(identifier: UUID, db: Database, user: ReadUser):
    return service.listing(db, user, identifier)


@router.post(
    "/connections/{identifier}/versions",
    response_model=AskDataVersionResponse,
    status_code=202,
)
def create(
    identifier: UUID,
    db: Database,
    user: ManageUser,
    body: AskDataVersionInput | None = None,
):
    return service.create(db, user, identifier, body or AskDataVersionInput())


@router.get("/connections/{identifier}/configuration", response_model=AskDataConfigurationResponse)
def configuration(identifier: UUID, db: Database, user: ReadUser):
    return service.configuration(db, user, identifier)


@router.get("/versions/{identifier}", response_model=AskDataVersionResponse)
def detail(identifier: UUID, db: Database, user: ReadUser):
    return service.detail(db, user, identifier)


@router.post("/versions/{identifier}/publish", response_model=AskDataVersionResponse)
def publish(identifier: UUID, db: Database, user: ManageUser):
    return service.publish(db, user, identifier)


@router.post("/versions/{identifier}/retire", response_model=AskDataVersionResponse)
def retire(identifier: UUID, db: Database, user: ManageUser):
    return service.retire(db, user, identifier)


@router.post("/versions/{identifier}/cancel", response_model=AskDataVersionResponse)
def cancel(identifier: UUID, db: Database, user: ManageUser):
    return service.cancel(db, user, identifier)
