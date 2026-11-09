from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response

from app.api.dependencies import Database, require_permission
from app.models import (
    User,
)
from app.schemas.conversations import (
    ConversationInput,
    ConversationResponse,
    EventResponse,
    MessageInput,
    MessageResponse,
    RunResponse,
)
from app.schemas.diagnosis import AnalysisResponse
from app.services.diagnosis import workbench as _service

router = APIRouter(prefix="/api/v1", tags=["analysis conversations"])

ReadUser = Annotated[User, Depends(require_permission("analyses:read"))]

RunUser = Annotated[User, Depends(require_permission("analyses:run"))]


@router.post(
    "/incidents/{incident_id}/analysis-conversations",
    response_model=ConversationResponse,
    status_code=201,
)
def create(incident_id: UUID, body: ConversationInput, db: Database, user: RunUser):
    return _service.create(incident_id, body, db, user)


@router.get(
    "/incidents/{incident_id}/analysis-conversations", response_model=list[ConversationResponse]
)
def listing(
    incident_id: UUID,
    response: Response,
    db: Database,
    user: ReadUser,
    owner_id: UUID | None = None,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
):
    return _service.listing(incident_id, response, db, user, owner_id, offset, limit)


@router.get("/analysis-conversations/{identifier}", response_model=ConversationResponse)
def detail(identifier: UUID, db: Database, user: ReadUser):
    return _service.detail(identifier, db, user)


@router.delete("/analysis-conversations/{identifier}", status_code=204)
def delete(identifier: UUID, db: Database, user: RunUser):
    _service.delete(identifier, db, user)
    return Response(status_code=204)


@router.post(
    "/analysis-conversations/{identifier}/messages", response_model=MessageResponse, status_code=202
)
def message(identifier: UUID, body: MessageInput, db: Database, user: RunUser):
    return _service.message(identifier, body, db, user)


@router.get("/analysis-conversations/{identifier}/messages", response_model=list[MessageResponse])
def messages(
    identifier: UUID,
    response: Response,
    db: Database,
    user: ReadUser,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
):
    return _service.messages(identifier, response, db, user, offset, limit)


@router.get("/analysis-conversations/{identifier}/runs", response_model=list[RunResponse])
def runs(
    identifier: UUID,
    response: Response,
    db: Database,
    user: ReadUser,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
):
    return _service.runs(identifier, response, db, user, offset, limit)


@router.get("/analysis-conversations/{identifier}/events", response_model=list[EventResponse])
def events(
    identifier: UUID,
    db: Database,
    user: ReadUser,
    after_seq: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
):
    return _service.events(identifier, db, user, after_seq, limit)


@router.get("/analysis-conversations/{identifier}/stream")
def stream(
    identifier: UUID,
    request: Request,
    db: Database,
    user: ReadUser,
    after_seq: int = Query(0, ge=0),
):
    return _service.stream(identifier, request, db, user, after_seq)


@router.get("/analysis-runs/{identifier}", response_model=RunResponse)
def run_detail(identifier: UUID, db: Database, user: ReadUser):
    return _service.run_detail(identifier, db, user)


@router.post("/analysis-runs/{identifier}/cancel", response_model=RunResponse)
def stop(identifier: UUID, db: Database, user: RunUser):
    return _service.stop(identifier, db, user)


@router.post("/analysis-runs/{identifier}/adopt", response_model=AnalysisResponse)
def publish(identifier: UUID, db: Database, user: RunUser):
    return _service.publish(identifier, db, user)
