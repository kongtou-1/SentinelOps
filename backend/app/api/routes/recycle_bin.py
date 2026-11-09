from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, Response

from app.api.dependencies import CurrentUser, Database
from app.schemas.recycle_bin import (
    DeleteInput,
    RecycleEntry,
    RecycleType,
    RestoreInput,
    RestoreResult,
)
from app.services import recycle_bin as service

router = APIRouter(prefix="/api/v1/system/recycle-bin", tags=["recycle-bin"])


@router.get("/types", response_model=list[RecycleType])
def types(db: Database, user: CurrentUser):
    return service.types(db, user)


@router.get("", response_model=list[RecycleEntry])
def listing(
    response: Response,
    db: Database,
    user: CurrentUser,
    kind: str | None = None,
    search: Annotated[str, Query(max_length=200)] = "",
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
):
    return service.listing(db, response, user, kind, search, offset, limit)


@router.post("/{kind}/{identifier}/restore", response_model=RestoreResult)
def restore(kind: str, identifier: UUID, body: RestoreInput, db: Database, user: CurrentUser):
    return service.restore(db, kind, identifier, body, user)


@router.delete("/{kind}/{identifier}", status_code=204)
def delete(kind: str, identifier: UUID, body: DeleteInput, db: Database, user: CurrentUser):
    service.delete(db, kind, identifier, body, user)
