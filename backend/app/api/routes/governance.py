from datetime import UTC, datetime, timedelta
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response
from pydantic import AwareDatetime

from app.api.dependencies import CurrentUser, Database, require_permission
from app.models import RetentionPlan, User
from app.schemas.agent_config import AgentModule
from app.schemas.governance import (
    ArchiveResponse,
    CleanupConfirm,
    CleanupPreview,
    CleanupResponse,
    Purpose,
)
from app.services.operations import retention, usage
from app.services.records import active_record

router = APIRouter(prefix="/api/v1", tags=["data-governance"])
ReadUser = Annotated[User, Depends(require_permission("system:read"))]
AdminUser = Annotated[User, Depends(require_permission("users:manage"))]
Offset = Annotated[int, Query(ge=0)]
Limit = Annotated[int, Query(ge=1, le=100)]


@router.get("/system/storage")
def storage(db: Database, user: ReadUser):
    return retention.storage(db)


@router.post("/system/cleanup-plans", response_model=CleanupResponse, status_code=201)
def preview(body: CleanupPreview, db: Database, user: AdminUser):
    return retention.preview(db, body, user)


@router.get("/system/cleanup-plans", response_model=list[CleanupResponse])
def plans(response: Response, db: Database, user: AdminUser, offset: Offset = 0, limit: Limit = 50):
    return retention.listing(db, response, "plans", offset, limit)


@router.get("/system/cleanup-plans/{identifier}", response_model=CleanupResponse)
def plan(identifier: UUID, db: Database, user: AdminUser):
    return active_record(db, RetentionPlan, identifier)


@router.post(
    "/system/cleanup-plans/{identifier}/execute", response_model=CleanupResponse, status_code=202
)
def execute(identifier: UUID, body: CleanupConfirm, db: Database, user: AdminUser):
    return retention.confirm(db, identifier, body, user)


@router.get("/archives", response_model=list[ArchiveResponse])
def archives(
    response: Response,
    db: Database,
    user: CurrentUser,
    resource_id: UUID | None = None,
    offset: Offset = 0,
    limit: Limit = 50,
):
    return retention.archive_listing(db, response, user, resource_id, offset, limit)


@router.get("/archives/{identifier}/content")
def content(identifier: UUID, db: Database, user: CurrentUser):
    return retention.archive_content(db, identifier, user)


def dates(start, end):
    now = datetime.now(UTC)
    return start or now - timedelta(days=30), end or now


@router.get("/system/model-usage")
def usage_records(
    response: Response,
    db: Database,
    user: ReadUser,
    start: AwareDatetime | None = None,
    end: AwareDatetime | None = None,
    application_id: UUID | None = None,
    purpose: Purpose | None = None,
    source: Annotated[
        str | None, Query(pattern=r"^(platform_gateway|llm_probe|application_metrics)$")
    ] = None,
    offset: Offset = 0,
    limit: Limit = 50,
    agent_module: AgentModule | None = None,
    agent_channel_id: UUID | None = None,
    model: str | None = None,
):
    start, end = dates(start, end)
    return usage.listing(
        db,
        response,
        start,
        end,
        application_id,
        purpose,
        source,
        offset,
        limit,
        agent_module,
        agent_channel_id,
        model,
    )


@router.get("/system/model-usage/summary")
def usage_summary(
    db: Database,
    user: ReadUser,
    start: AwareDatetime | None = None,
    end: AwareDatetime | None = None,
    application_id: UUID | None = None,
    purpose: Purpose | None = None,
    agent_module: AgentModule | None = None,
    agent_channel_id: UUID | None = None,
    model: str | None = None,
):
    start, end = dates(start, end)
    return usage.summary(
        db, start, end, application_id, purpose, agent_module, agent_channel_id, model
    )
