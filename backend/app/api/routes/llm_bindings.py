"""Shared LLM dependencies and per-service use notes; no deployment or probe changes."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response

from app.api.dependencies import Database, require_permission
from app.models import User
from app.schemas.llm import LLMBindingInput, LLMBindingResponse
from app.services.llm import bindings as _service

router = APIRouter(
    prefix="/api/v1",
    tags=["llm-bindings"],
    dependencies=[Depends(require_permission("resources:read"))],
)

ReadUser = Annotated[User, Depends(require_permission("llm:read"))]

ManageUser = Annotated[User, Depends(require_permission("llm:manage"))]


@router.get("/llm-bindings", response_model=list[LLMBindingResponse])
def bindings(
    response: Response,
    db: Database,
    user: ReadUser,
    application_id: UUID | None = None,
    llm_endpoint_id: UUID | None = None,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
):
    return _service.bindings(response, db, user, application_id, llm_endpoint_id, offset, limit)


@router.put(
    "/llm-endpoints/{endpoint_id}/applications/{application_id}",
    response_model=LLMBindingResponse,
)
def bind(
    endpoint_id: UUID, application_id: UUID, body: LLMBindingInput, db: Database, user: ManageUser
):
    return _service.bind(endpoint_id, application_id, body, db, user)


@router.delete("/llm-endpoints/{endpoint_id}/applications/{application_id}", status_code=204)
def unbind(endpoint_id: UUID, application_id: UUID, db: Database, user: ManageUser):
    return _service.unbind(endpoint_id, application_id, db, user)
