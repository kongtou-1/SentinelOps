"""Ask-data usage statistics (administrator aggregate view)."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from app.api.dependencies import Database, require_permission
from app.models import User
from app.schemas.askdata_stage5 import AskDataStatisticsResponse
from app.services import askdata_statistics as service

router = APIRouter()
ManageUser = Annotated[User, Depends(require_permission("askdata:manage"))]


@router.get("/statistics", response_model=AskDataStatisticsResponse)
def statistics(
    db: Database,
    user: ManageUser,
    connection_id: UUID | None = None,
    days: int = Query(default=30, ge=1, le=180),
):
    return service.statistics(db, user, connection_id, days)
