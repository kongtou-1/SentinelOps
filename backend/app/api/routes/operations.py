from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response

from app.api.dependencies import Database, require_permission
from app.models import User
from app.schemas.operations import (
    ObjectiveInput,
    ObjectiveResponse,
    OperationsPolicyInput,
    OperationsPolicyResponse,
    RelationDecision,
    RelationResponse,
)
from app.services.operations import impact, objectives

router = APIRouter(prefix="/api/v1", tags=["operations"])
ReadUser = Annotated[User, Depends(require_permission("resources:read"))]
ManageUser = Annotated[User, Depends(require_permission("resources:manage"))]
HandleUser = Annotated[User, Depends(require_permission("incidents:handle"))]


@router.get(
    "/business-services/{application_id}/operations-policy", response_model=OperationsPolicyResponse
)
def read_policy(application_id: UUID, db: Database, user: ReadUser):
    return impact.read_policy(db, application_id)


@router.put(
    "/business-services/{application_id}/operations-policy", response_model=OperationsPolicyResponse
)
def update_policy(
    application_id: UUID, body: OperationsPolicyInput, db: Database, user: ManageUser
):
    return impact.configure_policy(db, application_id, body, user)


@router.get("/business-services/{application_id}/impact")
def service_impact(application_id: UUID, db: Database, user: ReadUser):
    return impact.impact(db, application_id)


@router.get("/incidents/{incident_id}/relations", response_model=list[RelationResponse])
def incident_relations(
    incident_id: UUID,
    response: Response,
    db: Database,
    user: ReadUser,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
):
    return impact.listing(db, incident_id, response, offset, limit)


@router.post("/incident-relations/{identifier}/decision", response_model=RelationResponse)
def decide_relation(identifier: UUID, body: RelationDecision, db: Database, user: HandleUser):
    return impact.decide(db, identifier, body, user)


@router.get(
    "/business-services/{application_id}/objectives", response_model=list[ObjectiveResponse]
)
def list_objectives(
    application_id: UUID,
    response: Response,
    db: Database,
    user: ReadUser,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
):
    return objectives.listing(db, application_id, response, offset, limit)


@router.post(
    "/business-services/{application_id}/objectives",
    response_model=ObjectiveResponse,
    status_code=201,
)
def create_objective(application_id: UUID, body: ObjectiveInput, db: Database, user: ManageUser):
    return objectives.configure(db, application_id, body, actor=user)


@router.put(
    "/business-services/{application_id}/objectives/{identifier}", response_model=ObjectiveResponse
)
def update_objective(
    application_id: UUID, identifier: UUID, body: ObjectiveInput, db: Database, user: ManageUser
):
    return objectives.configure(db, application_id, body, identifier, user)


@router.delete("/service-objectives/{identifier}", status_code=204)
def delete_objective(
    identifier: UUID, expected_version: Annotated[int, Query(ge=1)], db: Database, user: ManageUser
):
    objectives.remove(db, identifier, expected_version, user)
