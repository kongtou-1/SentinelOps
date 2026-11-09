from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from pydantic import AwareDatetime

from app.api.dependencies import Database, require_permission
from app.models import User
from app.schemas.llm import (
    EndpointCreate,
    EndpointInput,
    EndpointResponse,
    LLMMetric,
    LLMStatusResponse,
    ProbeResult,
)
from app.services.llm import management as _service

router = APIRouter(prefix="/api/v1", tags=["llm"])

ReadUser = Annotated[User, Depends(require_permission("llm:read"))]

ManageUser = Annotated[User, Depends(require_permission("llm:manage"))]

ProbeUser = Annotated[User, Depends(require_permission("llm:probe"))]


@router.get("/llm-endpoints", response_model=list[EndpointResponse])
def endpoints(
    response: Response,
    db: Database,
    user: ReadUser,
    q: Annotated[str | None, Query(max_length=100)] = None,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
):
    return _service.endpoints(response, db, user, q, offset, limit)


@router.post("/llm-endpoints", response_model=EndpointResponse, status_code=201)
def create_endpoint(body: EndpointCreate, request: Request, db: Database, user: ManageUser):
    return _service.create_endpoint(body, request, db, user)


@router.get("/llm-endpoints/{endpoint_id}", response_model=EndpointResponse)
def endpoint_detail(endpoint_id: UUID, db: Database, user: ReadUser):
    return _service.endpoint_detail(endpoint_id, db, user)


@router.put("/llm-endpoints/{endpoint_id}", response_model=EndpointResponse)
def update_endpoint(
    endpoint_id: UUID,
    body: EndpointInput,
    request: Request,
    db: Database,
    user: ManageUser,
):
    return _service.update_endpoint(endpoint_id, body, request, db, user)


@router.get("/llm-endpoints/{endpoint_id}/status", response_model=LLMStatusResponse)
def endpoint_status_summary(endpoint_id: UUID, db: Database, user: ReadUser):
    return _service.endpoint_status_summary(endpoint_id, db, user)


@router.delete("/llm-endpoints/{endpoint_id}", status_code=204)
def delete_endpoint(endpoint_id: UUID, request: Request, db: Database, user: ManageUser):
    return _service.delete_endpoint(endpoint_id, request, db, user)


@router.post("/llm-endpoints/{endpoint_id}/probe", response_model=ProbeResult)
async def probe_endpoint(endpoint_id: UUID, request: Request, user: ProbeUser):
    return await _service.probe_endpoint(endpoint_id, request, user)


@router.get("/monitoring/llm-metrics")
def export_metrics(
    request: Request, db: Database, authorization: Annotated[str | None, Header()] = None
):
    return _service.export_metrics(request, db, authorization)


@router.get("/llm-endpoints/{endpoint_id}/metrics")
def endpoint_metrics(
    endpoint_id: UUID,
    db: Database,
    user: ReadUser,
    metric: LLMMetric = "llm_success_rate",
    start: AwareDatetime | None = None,
    end: AwareDatetime | None = None,
    step: Annotated[int, Query(ge=15, le=86400)] = 30,
):
    return _service.endpoint_metrics(endpoint_id, db, user, metric, start, end, step)
