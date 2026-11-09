from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Query, Response

from app.api.dependencies import CurrentUser, Database
from app.schemas.access import (
    AccessChecks,
    AccessDecision,
    AccessGrantResponse,
    AccessRequestInput,
    AccessRequestResponse,
    PolicyInput,
    PolicyResponse,
    ResourceKind,
    RolePermissionsInput,
)
from app.services import access as service

router = APIRouter(prefix="/api/v1", tags=["access"])
Offset = Annotated[int, Query(ge=0)]
Limit = Annotated[int, Query(ge=1, le=100)]


@router.get("/permissions")
def permissions(user: CurrentUser):
    return service.catalogue(user)


@router.put("/roles/{code}/permissions")
def role_permissions(code: str, body: RolePermissionsInput, db: Database, user: CurrentUser):
    return service.update_role(db, user, code, body)


@router.get("/access-targets")
def targets(
    db: Database,
    user: CurrentUser,
    response: Response,
    kind: ResourceKind,
    q: str = Query(default="", max_length=100),
    offset: Offset = 0,
    limit: Limit = 50,
):
    return service.targets(db, user, kind, q, offset, limit, response)


@router.get("/access-requests", response_model=list[AccessRequestResponse])
def requests(
    db: Database,
    user: CurrentUser,
    response: Response,
    status: Literal["pending", "approved", "rejected", "withdrawn", "invalidated"] | None = None,
    offset: Offset = 0,
    limit: Limit = 50,
):
    return service.requests(db, user, response, status, offset, limit)


@router.post("/access-requests", response_model=AccessRequestResponse, status_code=201)
def create_request(body: AccessRequestInput, db: Database, user: CurrentUser):
    return service.create_request(db, user, body)


@router.get("/access-requests/{identifier}", response_model=AccessRequestResponse)
def request_detail(identifier: UUID, db: Database, user: CurrentUser):
    return service.request_detail(db, user, identifier)


@router.post("/access-requests/{identifier}/approve", response_model=AccessRequestResponse)
def approve(identifier: UUID, body: AccessDecision, db: Database, user: CurrentUser):
    return service.decide(db, user, identifier, body, True)


@router.post("/access-requests/{identifier}/reject", response_model=AccessRequestResponse)
def reject(identifier: UUID, body: AccessDecision, db: Database, user: CurrentUser):
    return service.decide(db, user, identifier, body, False)


@router.post("/access-requests/{identifier}/withdraw", response_model=AccessRequestResponse)
def withdraw(identifier: UUID, db: Database, user: CurrentUser):
    return service.withdraw(db, user, identifier)


@router.get("/access-grants", response_model=list[AccessGrantResponse])
def grants(
    db: Database, user: CurrentUser, response: Response, offset: Offset = 0, limit: Limit = 50
):
    return service.grants(db, user, response, offset, limit)


@router.post("/access-grants/{identifier}/revoke", status_code=204)
def revoke(identifier: UUID, db: Database, user: CurrentUser):
    service.revoke(db, user, identifier)


@router.get("/access-policies/{kind}/{identifier}", response_model=PolicyResponse)
def policy(
    kind: ResourceKind,
    identifier: str,
    db: Database,
    user: CurrentUser,
    user_id: UUID | None = None,
):
    return service.policy(db, user, kind, identifier, user_id=user_id)


@router.put("/access-policies/{kind}/{identifier}", response_model=PolicyResponse)
def update_policy(
    kind: ResourceKind,
    identifier: str,
    body: PolicyInput,
    db: Database,
    user: CurrentUser,
    user_id: UUID | None = None,
):
    return service.policy(db, user, kind, identifier, body, user_id=user_id)


@router.post("/access/check")
def checks(body: AccessChecks, db: Database, user: CurrentUser):
    return service.checks(db, user, body)
