"""Saved-analysis endpoints (owner-scoped, replayable under current grants)."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from app.api.dependencies import Database, require_permission
from app.models import User
from app.schemas.askdata_stage5 import (
    AskDataAnalysisCreate,
    AskDataAnalysisPage,
    AskDataAnalysisResponse,
    AskDataAnalysisRunResponse,
    AskDataAnalysisUpdate,
)
from app.services import askdata_analyses as service

router = APIRouter()
ReadUser = Annotated[User, Depends(require_permission("askdata:read"))]
RunUser = Annotated[User, Depends(require_permission("askdata:run"))]


@router.get("/analyses", response_model=AskDataAnalysisPage)
def listing(
    db: Database,
    user: ReadUser,
    connection_id: UUID | None = None,
    search: str = Query(default="", max_length=200),
    category: str = Query(default="", max_length=60),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
):
    rows, total, categories = service.listing(
        db, user, connection_id, search, category, offset, limit
    )
    return AskDataAnalysisPage(
        items=[AskDataAnalysisResponse.model_validate(row) for row in rows],
        total=total,
        categories=categories,
    )


@router.post("/analyses", response_model=AskDataAnalysisResponse, status_code=201)
def create(body: AskDataAnalysisCreate, db: Database, user: RunUser):
    return service.create(db, user, body)


@router.get("/analyses/{identifier}", response_model=AskDataAnalysisResponse)
def detail(identifier: UUID, db: Database, user: ReadUser):
    return service.detail(db, user, identifier)


@router.put("/analyses/{identifier}", response_model=AskDataAnalysisResponse)
def update(identifier: UUID, body: AskDataAnalysisUpdate, db: Database, user: RunUser):
    return service.update(db, user, identifier, body)


@router.delete("/analyses/{identifier}", status_code=204)
def remove(identifier: UUID, db: Database, user: RunUser):
    service.delete(db, user, identifier)


@router.post("/analyses/{identifier}/execute", response_model=AskDataAnalysisRunResponse)
def execute(identifier: UUID, db: Database, user: RunUser):
    return service.execute(db, user, identifier)
