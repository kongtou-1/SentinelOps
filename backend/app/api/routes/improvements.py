from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response

from app.api.dependencies import Database, require_permission
from app.models import ImprovementTask, User
from app.schemas.diagnosis import DocumentResponse, SkillResponse
from app.schemas.improvements import (
    ImportImprovements,
    ImprovementDecision,
    ImprovementResponse,
    ImprovementSubmit,
    ImprovementUpdate,
    KnowledgeDraft,
    KnowledgeValidity,
)
from app.services.operations import improvements
from app.services.records import active_record

router = APIRouter(prefix="/api/v1", tags=["improvements"])
ReadUser = Annotated[User, Depends(require_permission("reports:read"))]
WriteUser = Annotated[User, Depends(require_permission("incidents:handle"))]
AdminUser = Annotated[User, Depends(require_permission("reports:manage"))]
KnowledgeAdmin = Annotated[User, Depends(require_permission("knowledge:manage"))]
Offset = Annotated[int, Query(ge=0)]
Limit = Annotated[int, Query(ge=1, le=100)]


@router.post("/reports/{identifier}/improvements", response_model=list[ImprovementResponse])
def import_items(identifier: UUID, body: ImportImprovements, db: Database, user: WriteUser):
    return improvements.import_items(db, identifier, body, user)


@router.get("/improvement-tasks", response_model=list[ImprovementResponse])
def tasks(
    response: Response,
    db: Database,
    user: ReadUser,
    report_id: UUID | None = None,
    status: Literal[
        "unassigned", "pending", "in_progress", "awaiting_review", "completed", "cancelled"
    ]
    | None = None,
    offset: Offset = 0,
    limit: Limit = 50,
):
    return improvements.listing(db, response, report_id, status, offset, limit)


@router.get("/improvement-tasks/{identifier}", response_model=ImprovementResponse)
def detail(identifier: UUID, db: Database, user: ReadUser):
    return active_record(db, ImprovementTask, identifier)


@router.put("/improvement-tasks/{identifier}", response_model=ImprovementResponse)
def update(identifier: UUID, body: ImprovementUpdate, db: Database, user: WriteUser):
    return improvements.update(db, identifier, body, user)


@router.post("/improvement-tasks/{identifier}/submit", response_model=ImprovementResponse)
def submit(identifier: UUID, body: ImprovementSubmit, db: Database, user: WriteUser):
    return improvements.submit(db, identifier, body, user)


@router.post("/improvement-tasks/{identifier}/decision", response_model=ImprovementResponse)
def decide(identifier: UUID, body: ImprovementDecision, db: Database, user: AdminUser):
    return improvements.decide(db, identifier, body, user)


@router.put("/knowledge/{identifier}/validity", response_model=DocumentResponse)
def document_validity(
    identifier: UUID, body: KnowledgeValidity, db: Database, user: KnowledgeAdmin
):
    return improvements.validity(db, "document", identifier, body, user)


@router.put("/skills/{identifier}/validity", response_model=SkillResponse)
def skill_validity(identifier: UUID, body: KnowledgeValidity, db: Database, user: KnowledgeAdmin):
    return improvements.validity(db, "skill", identifier, body, user)


@router.post(
    "/reports/{identifier}/knowledge-draft", response_model=DocumentResponse, status_code=201
)
def knowledge_draft(identifier: UUID, body: KnowledgeDraft, db: Database, user: KnowledgeAdmin):
    return improvements.draft_knowledge(db, identifier, body, user)
