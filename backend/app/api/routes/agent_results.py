from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query

from app.api.dependencies import CurrentUser, Database
from app.schemas.agent_results import (
    Business,
    ResultDetail,
    ResultKind,
    ResultKnowledgeDraft,
    ResultPage,
    Section,
)
from app.schemas.diagnosis import DocumentResponse
from app.services.knowledge import agent_results as service

router = APIRouter(prefix="/api/v1/agent-results", tags=["agent-results"])


@router.get("", response_model=ResultPage)
def listing(
    db: Database,
    user: CurrentUser,
    business: Business,
    scope_id: UUID,
    query: str = Query(default="", max_length=200),
    conversation_id: UUID | None = None,
    run_id: UUID | None = None,
    offset: Annotated[int, Query(ge=0, le=10000)] = 0,
    limit: Annotated[int, Query(ge=1, le=20)] = 10,
):
    return service.listing(
        db, user, business, scope_id, query, offset, limit, conversation_id, run_id
    )


@router.get("/{kind}/{identifier}", response_model=ResultDetail)
def detail(
    kind: ResultKind,
    identifier: UUID,
    db: Database,
    user: CurrentUser,
    section: Section = "result",
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=8000)] = 4000,
):
    return service.detail(db, user, kind, identifier, section, offset, limit)


@router.post(
    "/{kind}/{identifier}/knowledge-draft", response_model=DocumentResponse, status_code=201
)
def draft(
    kind: ResultKind, identifier: UUID, body: ResultKnowledgeDraft, db: Database, user: CurrentUser
):
    return service.draft(db, user, kind, identifier, body)
