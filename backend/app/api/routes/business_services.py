"""Business grouping changes resource metadata only, never deployment or server state."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, Response

from app.api.dependencies import Database
from app.api.routes.resources import ManageUser, ReadUser
from app.schemas.resources import (
    BusinessServiceMembers,
    BusinessServiceResponse,
)
from app.schemas.sources import ServiceCreateInput, ServiceUpdateInput
from app.services.resources import business_services as _service

router = APIRouter(prefix="/api/v1", tags=["business-services"])


@router.get("/business-services", response_model=list[BusinessServiceResponse])
def services(
    response: Response,
    db: Database,
    user: ReadUser,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
):
    return _service.services(response, db, user, offset, limit)


@router.post(
    "/servers/{server_id}/business-services",
    response_model=BusinessServiceResponse,
    status_code=201,
)
def create_service(server_id: UUID, body: ServiceCreateInput, db: Database, user: ManageUser):
    return _service.create_service(server_id, body, db, user)


@router.put(
    "/servers/{server_id}/business-services/{application_id}",
    response_model=BusinessServiceResponse,
)
def update_service(
    server_id: UUID,
    application_id: UUID,
    body: ServiceUpdateInput,
    db: Database,
    user: ManageUser,
):
    return _service.update_service(server_id, application_id, body, db, user)


@router.put(
    "/servers/{server_id}/business-services/{application_id}/containers",
    response_model=BusinessServiceResponse,
)
def assign_service(
    server_id: UUID,
    application_id: UUID,
    body: BusinessServiceMembers,
    db: Database,
    user: ManageUser,
):
    return _service.assign_service(server_id, application_id, body, db, user)
