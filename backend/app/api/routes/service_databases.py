from uuid import UUID

from fastapi import APIRouter, Response

from app.api.dependencies import Database
from app.api.routes.resources import ManageUser, ReadUser
from app.schemas.service_databases import (
    DatabaseInput,
    DatabaseResponse,
    DatabaseSchemaResponse,
    DatabaseTestInput,
    DatabaseTestResponse,
)
from app.services import service_databases as service

router = APIRouter(prefix="/api/v1", tags=["service-databases"])


@router.get("/service-databases", response_model=list[DatabaseResponse])
def all_connections(db: Database, user: ReadUser):
    return service.listing(db)


@router.post("/service-databases", response_model=DatabaseResponse, status_code=201)
def create_connection(body: DatabaseInput, db: Database, user: ManageUser):
    return service.configure(db, body.application_id, body, user)


@router.get("/business-services/{application_id}/databases", response_model=list[DatabaseResponse])
def listing(application_id: UUID, db: Database, user: ReadUser):
    return service.listing(db, application_id)


@router.post(
    "/business-services/{application_id}/databases",
    response_model=DatabaseResponse,
    status_code=201,
)
def create(application_id: UUID, body: DatabaseInput, db: Database, user: ManageUser):
    return service.configure(db, application_id, body, user)


@router.post("/service-databases/test", response_model=DatabaseTestResponse)
def test(body: DatabaseTestInput, db: Database, user: ManageUser):
    return service.test(db, body, user)


@router.get("/service-databases/{identifier}", response_model=DatabaseResponse)
def detail(identifier: UUID, db: Database, user: ReadUser):
    return service.response(service.active(db, identifier))


@router.put("/service-databases/{identifier}", response_model=DatabaseResponse)
def update(identifier: UUID, body: DatabaseInput, db: Database, user: ManageUser):
    row = service.active(db, identifier)
    return service.configure(db, row.application_id, body, user, identifier)


@router.delete("/service-databases/{identifier}", status_code=204)
def remove(identifier: UUID, db: Database, user: ManageUser):
    service.remove(db, identifier, user)
    return Response(status_code=204)


@router.get("/service-databases/{identifier}/schema", response_model=DatabaseSchemaResponse)
def schema(identifier: UUID, db: Database, user: ReadUser):
    return service.schema_response(service.active(db, identifier))


@router.post(
    "/service-databases/{identifier}/schema/refresh", response_model=DatabaseSchemaResponse
)
def refresh(identifier: UUID, db: Database, user: ManageUser):
    return service.refresh_schema(db, identifier, user)
