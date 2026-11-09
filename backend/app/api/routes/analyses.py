from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response

from app.api.dependencies import Database, require_permission
from app.models import User
from app.schemas.diagnosis import AnalysisResponse, AnalyzeInput
from app.services.diagnosis import management as _service

router = APIRouter(prefix="/api/v1", tags=["analyses"])

ReadUser = Annotated[User, Depends(require_permission("analyses:read"))]

RunUser = Annotated[User, Depends(require_permission("analyses:run"))]


@router.post("/incidents/{incident_id}/analyze", response_model=AnalysisResponse, status_code=202)
def analyze(incident_id: UUID, body: AnalyzeInput, response: Response, db: Database, user: RunUser):
    return _service.analyze(incident_id, body, response, db, user)


@router.get("/incidents/{incident_id}/analysis", response_model=AnalysisResponse)
def current(incident_id: UUID, db: Database, user: ReadUser):
    return _service.current(incident_id, db, user)


@router.get("/incidents/{incident_id}/analyses", response_model=list[AnalysisResponse])
def history(
    response: Response,
    incident_id: UUID,
    db: Database,
    user: ReadUser,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
):
    return _service.history(response, incident_id, db, user, offset, limit)


@router.get("/analyses/{analysis_id}", response_model=AnalysisResponse)
def detail(analysis_id: UUID, db: Database, user: ReadUser):
    return _service.detail(analysis_id, db, user)
