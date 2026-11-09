"""Data catalog and semantic model browsing endpoints."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from app.api.dependencies import Database, require_permission
from app.models import User
from app.schemas.askdata import (
    AskDataCatalogSummary,
    AskDataRelationshipPage,
    AskDataTableDetail,
    AskDataTablePage,
    AskDataTermDetail,
    AskDataTermPage,
)
from app.services import askdata_catalog as service

router = APIRouter()
ReadUser = Annotated[User, Depends(require_permission("askdata:read"))]


@router.get("/versions/{identifier}/catalog", response_model=AskDataCatalogSummary)
def catalog(identifier: UUID, db: Database, user: ReadUser):
    return service.summary(db, user, identifier)


@router.get("/versions/{identifier}/tables", response_model=AskDataTablePage)
def tables(
    identifier: UUID,
    db: Database,
    user: ReadUser,
    search: str = Query(default="", max_length=200),
    schema: str | None = Query(default=None, max_length=200),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
):
    return service.tables(db, user, identifier, search, schema, offset, limit)


@router.get("/versions/{identifier}/tables/{table_id}", response_model=AskDataTableDetail)
def table(identifier: UUID, table_id: str, db: Database, user: ReadUser):
    return service.table(db, user, identifier, table_id)


@router.get("/versions/{identifier}/terms", response_model=AskDataTermPage)
def terms(
    identifier: UUID,
    db: Database,
    user: ReadUser,
    search: str = Query(default="", max_length=200),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
):
    return service.terms(db, user, identifier, search, offset, limit)


@router.get("/versions/{identifier}/terms/{term_id}", response_model=AskDataTermDetail)
def term(identifier: UUID, term_id: str, db: Database, user: ReadUser):
    return service.term(db, user, identifier, term_id)


@router.get("/versions/{identifier}/relationships", response_model=AskDataRelationshipPage)
def relationships(
    identifier: UUID,
    db: Database,
    user: ReadUser,
    limit: int = Query(default=500, ge=1, le=1000),
):
    return service.relationships(db, user, identifier, limit)
