from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Response

from app.api.dependencies import Database, require_permission
from app.models import User
from app.schemas.platform import BackupInput, BackupResponse
from app.services.operations import platform

router = APIRouter(prefix="/api/v1/system", tags=["platform"])
ReadUser = Annotated[User, Depends(require_permission("system:read"))]
AdminUser = Annotated[User, Depends(require_permission("users:manage"))]


@router.get("/components")
def components(db: Database, user: ReadUser):
    return platform.status(db)


@router.get("/backup-records", response_model=list[BackupResponse])
def backups(
    response: Response,
    db: Database,
    user: ReadUser,
    kind: Literal["backup", "restore"] | None = None,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
):
    return platform.listing(db, response, kind, offset, limit)


@router.post("/backup-records", response_model=BackupResponse, status_code=201)
def register(body: BackupInput, db: Database, user: AdminUser):
    return platform.register_backup(db, body, user)
