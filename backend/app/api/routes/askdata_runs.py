"""Ask-data conversation, run, event, cancellation and export endpoints."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response

from app.api.dependencies import Database, require_permission
from app.models import User
from app.schemas.askdata_runs import (
    AskDataConversationDetail,
    AskDataConversationInput,
    AskDataConversationResponse,
    AskDataEventPage,
    AskDataRunDetail,
    AskDataRunInput,
    AskDataRunResponse,
)
from app.services import askdata_runs as service

router = APIRouter()
ReadUser = Annotated[User, Depends(require_permission("askdata:read"))]
RunUser = Annotated[User, Depends(require_permission("askdata:run"))]


@router.get("/conversations", response_model=list[AskDataConversationResponse])
def conversations(
    db: Database,
    user: ReadUser,
    connection_id: UUID | None = None,
    limit: int = 50,
    offset: int = 0,
):
    return service.listing(db, user, connection_id, max(1, min(limit, 100)), max(0, offset))


@router.post("/conversations", response_model=AskDataConversationResponse, status_code=201)
def create_conversation(body: AskDataConversationInput, db: Database, user: RunUser):
    return service.create_conversation(db, user, body)


@router.get("/conversations/{identifier}", response_model=AskDataConversationDetail)
def conversation_detail(identifier: UUID, db: Database, user: ReadUser):
    return service.detail(db, user, identifier)


@router.delete("/conversations/{identifier}", status_code=204)
def delete_conversation(identifier: UUID, db: Database, user: RunUser):
    service.delete_conversation(db, user, identifier)
    return Response(status_code=204)


@router.post(
    "/conversations/{identifier}/runs",
    response_model=AskDataRunResponse,
    status_code=202,
)
def submit(identifier: UUID, body: AskDataRunInput, db: Database, user: RunUser):
    return service.submit(db, user, identifier, body)


@router.get("/runs/{identifier}", response_model=AskDataRunDetail)
def run_detail(identifier: UUID, db: Database, user: ReadUser):
    return service.run_detail(db, user, identifier)


@router.get("/runs/{identifier}/events", response_model=AskDataEventPage)
def events(identifier: UUID, db: Database, user: ReadUser, after_seq: int = 0, limit: int = 100):
    return service.events(db, user, identifier, max(0, after_seq), max(1, min(limit, 200)))


@router.get("/runs/{identifier}/stream")
def stream(identifier: UUID, request: Request, db: Database, user: ReadUser, after_seq: int = 0):
    return service.stream(identifier, request, db, user, max(0, after_seq))


@router.post("/runs/{identifier}/cancel", response_model=AskDataRunResponse)
def cancel(identifier: UUID, db: Database, user: RunUser):
    return service.cancel(db, user, identifier)


@router.get("/runs/{identifier}/export")
def export(identifier: UUID, db: Database, user: ReadUser):
    return service.export_csv(db, user, identifier)
