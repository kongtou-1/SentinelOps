from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response

from app.api.dependencies import Database, require_permission
from app.models import User
from app.schemas.diagnosis import DocumentInput, DocumentResponse, SkillInput, SkillResponse
from app.services.knowledge import management as _service

router = APIRouter(prefix="/api/v1", tags=["knowledge"])

ReadUser = Annotated[User, Depends(require_permission("knowledge:read"))]

ManageUser = Annotated[User, Depends(require_permission("knowledge:manage"))]

Offset = Annotated[int, Query(ge=0)]

Limit = Annotated[int, Query(ge=1, le=100)]


@router.get("/knowledge", response_model=list[DocumentResponse])
def documents(
    response: Response, db: Database, user: ReadUser, offset: Offset = 0, limit: Limit = 50
):
    return _service.documents(response, db, user, offset, limit)


@router.get("/knowledge/search")
def retrieve(
    db: Database,
    user: ReadUser,
    q: Annotated[str, Query(min_length=1, max_length=1000)],
    tag: Annotated[str | None, Query(max_length=50)] = None,
    limit: Annotated[int, Query(ge=1, le=10)] = 5,
):
    return _service.retrieve(db, user, q, tag, limit)


@router.post("/knowledge", response_model=DocumentResponse, status_code=202)
def create_document(body: DocumentInput, db: Database, user: ManageUser):
    return _service.create_document(body, db, user)


@router.get("/knowledge/{document_id}", response_model=DocumentResponse)
def document(document_id: UUID, db: Database, user: ReadUser):
    return _service.document(document_id, db, user)


@router.put("/knowledge/{document_id}", response_model=DocumentResponse, status_code=202)
def replace_document(document_id: UUID, body: DocumentInput, db: Database, user: ManageUser):
    return _service.replace_document(document_id, body, db, user)


@router.post("/knowledge/{document_id}/reindex", response_model=DocumentResponse, status_code=202)
def reindex(document_id: UUID, db: Database, user: ManageUser):
    return _service.reindex(document_id, db, user)


@router.delete("/knowledge/{document_id}", status_code=204)
def delete_document(document_id: UUID, db: Database, user: ManageUser):
    return _service.delete_document(document_id, db, user)


@router.get("/skills", response_model=list[SkillResponse])
def skills(response: Response, db: Database, user: ReadUser, offset: Offset = 0, limit: Limit = 50):
    return _service.skills(response, db, user, offset, limit)


@router.post("/skills", response_model=SkillResponse, status_code=201)
def create_skill(body: SkillInput, db: Database, user: ManageUser):
    return _service.create_skill(body, db, user)


@router.get("/skills/{skill_id}", response_model=SkillResponse)
def skill(skill_id: UUID, db: Database, user: ReadUser):
    return _service.skill(skill_id, db, user)


@router.put("/skills/{skill_id}", response_model=SkillResponse)
def replace_skill(skill_id: UUID, body: SkillInput, db: Database, user: ManageUser):
    return _service.replace_skill(skill_id, body, db, user)


@router.delete("/skills/{skill_id}", status_code=204)
def delete_skill(skill_id: UUID, db: Database, user: ManageUser):
    return _service.delete_skill(skill_id, db, user)
