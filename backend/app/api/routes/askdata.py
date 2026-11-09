from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends

from app.api.dependencies import Database, require_permission
from app.api.routes import (
    askdata_analyses,
    askdata_catalog,
    askdata_code_rules,
    askdata_definitions,
    askdata_interchange,
    askdata_models,
    askdata_runs,
    askdata_settings,
    askdata_statistics,
)
from app.models import User
from app.schemas.askdata import (
    AskDataConfigurationInput,
    AskDataConfigurationResponse,
    AskDataConnectionResponse,
    AskDataContextResponse,
)
from app.services import askdata as service

router = APIRouter(prefix="/api/v1/askdata", tags=["askdata"])
router.include_router(askdata_models.router)
router.include_router(askdata_catalog.router)
router.include_router(askdata_definitions.router)
router.include_router(askdata_code_rules.router)
router.include_router(askdata_runs.router)
router.include_router(askdata_analyses.router)
router.include_router(askdata_settings.router)
router.include_router(askdata_interchange.router)
router.include_router(askdata_statistics.router)
ReadUser = Annotated[User, Depends(require_permission("askdata:read"))]
ManageUser = Annotated[User, Depends(require_permission("askdata:manage"))]


@router.get("/settings/connections", response_model=list[AskDataConnectionResponse])
def connections(db: Database, user: ReadUser, application_id: UUID | None = None):
    return service.connections(db, user, application_id)


@router.get("/settings/connections/{identifier}", response_model=AskDataContextResponse)
def context(identifier: UUID, db: Database, user: ReadUser):
    return service.connection_context(db, user, identifier)


@router.put("/settings/connections/{identifier}", response_model=AskDataConfigurationResponse)
def configure(identifier: UUID, body: AskDataConfigurationInput, db: Database, user: ManageUser):
    return service.configure(db, user, identifier, body)
