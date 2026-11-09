"""Business-model definition review, metric and version-diff endpoints."""

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from app.api.dependencies import Database, require_permission
from app.models import User
from app.schemas.askdata_definitions import (
    AskDataDefinitionInput,
    AskDataDefinitionPage,
    AskDataDefinitionResponse,
    AskDataMetricPage,
    AskDataVersionDiff,
)
from app.services import askdata_definitions as service

router = APIRouter()
ReadUser = Annotated[User, Depends(require_permission("askdata:read"))]
ManageUser = Annotated[User, Depends(require_permission("askdata:manage"))]


@router.get("/versions/{identifier}/definitions", response_model=AskDataDefinitionPage)
def definitions(
    identifier: UUID,
    db: Database,
    user: ReadUser,
    review_required: bool | None = Query(default=None),
    kind: Literal["term", "attribute", "metric"] | None = Query(default=None),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
):
    return service.listing(
        db,
        user,
        identifier,
        review_required=review_required,
        kind=kind,
        offset=offset,
        limit=limit,
    )


@router.put("/versions/{identifier}/definitions", response_model=AskDataDefinitionResponse)
def revise(identifier: UUID, body: AskDataDefinitionInput, db: Database, user: ManageUser):
    return service.upsert(db, user, identifier, body)


@router.post(
    "/versions/{identifier}/definitions/{definition_id}/resolve",
    response_model=AskDataDefinitionResponse,
)
def resolve(identifier: UUID, definition_id: UUID, db: Database, user: ManageUser):
    return service.resolve(db, user, identifier, definition_id)


@router.get("/versions/{identifier}/metrics", response_model=AskDataMetricPage)
def metrics(
    identifier: UUID,
    db: Database,
    user: ReadUser,
    search: str = Query(default="", max_length=200),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
):
    return service.metrics(db, user, identifier, search, offset, limit)


@router.get("/versions/{identifier}/diff", response_model=AskDataVersionDiff)
def diff(identifier: UUID, db: Database, user: ReadUser, compare: UUID = Query(...)):
    return service.diff(db, user, identifier, compare)
