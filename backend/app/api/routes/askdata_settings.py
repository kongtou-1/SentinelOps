"""Governance settings: zones, tags, rules, sensitive fields and supplements."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends

from app.api.dependencies import Database, require_permission
from app.models import User
from app.schemas.askdata_stage5 import (
    AskDataConnectionTestResponse,
    AskDataExecutionSettingsInput,
    AskDataExecutionSettingsResponse,
    AskDataRuleInput,
    AskDataRulePage,
    AskDataRuleResponse,
    AskDataSensitiveFieldInput,
    AskDataSensitiveFieldResponse,
    AskDataSensitivePage,
    AskDataSupplementInput,
    AskDataSupplementPage,
    AskDataSupplementResponse,
    AskDataTagInput,
    AskDataTagPage,
    AskDataTagResponse,
    AskDataZoneInput,
    AskDataZonePage,
    AskDataZoneResponse,
)
from app.services import askdata_settings as service

router = APIRouter()
ReadUser = Annotated[User, Depends(require_permission("askdata:read"))]
ManageUser = Annotated[User, Depends(require_permission("askdata:manage"))]


# ---------------------------------------------------------------------------
# Execution settings
# ---------------------------------------------------------------------------


@router.get(
    "/settings/{connection_id}/execution",
    response_model=AskDataExecutionSettingsResponse,
)
def execution(connection_id: UUID, db: Database, user: ReadUser):
    return service.execution_settings(db, user, connection_id)


@router.put(
    "/settings/{connection_id}/execution",
    response_model=AskDataExecutionSettingsResponse,
)
def update_execution(
    connection_id: UUID,
    body: AskDataExecutionSettingsInput,
    db: Database,
    user: ManageUser,
):
    return service.update_execution(db, user, connection_id, body)


@router.post(
    "/settings/{connection_id}/execution/test",
    response_model=AskDataConnectionTestResponse,
)
def test_execution(connection_id: UUID, db: Database, user: ManageUser):
    return service.test_execution(db, user, connection_id)


# ---------------------------------------------------------------------------
# Zones
# ---------------------------------------------------------------------------


@router.get("/settings/{connection_id}/zones", response_model=AskDataZonePage)
def zones(connection_id: UUID, db: Database, user: ReadUser):
    rows = service.zones(db, user, connection_id)
    return AskDataZonePage(
        items=[AskDataZoneResponse.model_validate(row) for row in rows],
        total=len(rows),
    )


@router.post("/settings/{connection_id}/zones", response_model=AskDataZoneResponse, status_code=201)
def create_zone(
    connection_id: UUID, body: AskDataZoneInput, db: Database, user: ManageUser
):
    return service.save_zone(db, user, connection_id, body)


@router.put(
    "/settings/{connection_id}/zones/{identifier}", response_model=AskDataZoneResponse
)
def update_zone(
    connection_id: UUID,
    identifier: UUID,
    body: AskDataZoneInput,
    db: Database,
    user: ManageUser,
):
    return service.save_zone(db, user, connection_id, body, identifier)


@router.delete("/settings/{connection_id}/zones/{identifier}", status_code=204)
def delete_zone(connection_id: UUID, identifier: UUID, db: Database, user: ManageUser):
    service.delete_zone(db, user, connection_id, identifier)


# ---------------------------------------------------------------------------
# Tags
# ---------------------------------------------------------------------------


def _tag_response(row, targets):
    return AskDataTagResponse(
        id=row.id,
        connection_id=row.connection_id,
        name=row.name,
        color=row.color,
        description=row.description,
        targets=targets,
        updated_at=row.updated_at,
    )


@router.get("/settings/{connection_id}/tags", response_model=AskDataTagPage)
def tags(connection_id: UUID, db: Database, user: ReadUser):
    rows = service.tags(db, user, connection_id)
    return AskDataTagPage(
        items=[_tag_response(row, targets) for row, targets in rows],
        total=len(rows),
    )


@router.post("/settings/{connection_id}/tags", response_model=AskDataTagResponse, status_code=201)
def create_tag(connection_id: UUID, body: AskDataTagInput, db: Database, user: ManageUser):
    row, targets = service.save_tag(db, user, connection_id, body)
    return _tag_response(row, targets)


@router.put(
    "/settings/{connection_id}/tags/{identifier}", response_model=AskDataTagResponse
)
def update_tag(
    connection_id: UUID,
    identifier: UUID,
    body: AskDataTagInput,
    db: Database,
    user: ManageUser,
):
    row, targets = service.save_tag(db, user, connection_id, body, identifier)
    return _tag_response(row, targets)


@router.delete("/settings/{connection_id}/tags/{identifier}", status_code=204)
def delete_tag(connection_id: UUID, identifier: UUID, db: Database, user: ManageUser):
    service.delete_tag(db, user, connection_id, identifier)


# ---------------------------------------------------------------------------
# Business rules
# ---------------------------------------------------------------------------


@router.get("/settings/{connection_id}/rules", response_model=AskDataRulePage)
def rules(connection_id: UUID, db: Database, user: ReadUser):
    rows = service.rules(db, user, connection_id)
    return AskDataRulePage(
        items=[AskDataRuleResponse.model_validate(row) for row in rows],
        total=len(rows),
    )


@router.post("/settings/{connection_id}/rules", response_model=AskDataRuleResponse, status_code=201)
def create_rule(connection_id: UUID, body: AskDataRuleInput, db: Database, user: ManageUser):
    return service.save_rule(db, user, connection_id, body)


@router.put(
    "/settings/{connection_id}/rules/{identifier}", response_model=AskDataRuleResponse
)
def update_rule(
    connection_id: UUID,
    identifier: UUID,
    body: AskDataRuleInput,
    db: Database,
    user: ManageUser,
):
    return service.save_rule(db, user, connection_id, body, identifier)


@router.delete("/settings/{connection_id}/rules/{identifier}", status_code=204)
def delete_rule(connection_id: UUID, identifier: UUID, db: Database, user: ManageUser):
    service.delete_rule(db, user, connection_id, identifier)


# ---------------------------------------------------------------------------
# Sensitive fields
# ---------------------------------------------------------------------------


@router.get(
    "/settings/{connection_id}/sensitive-fields", response_model=AskDataSensitivePage
)
def sensitive_fields(connection_id: UUID, db: Database, user: ReadUser):
    rows = service.sensitive_fields(db, user, connection_id)
    return AskDataSensitivePage(
        items=[AskDataSensitiveFieldResponse.model_validate(row) for row in rows],
        total=len(rows),
    )


@router.post(
    "/settings/{connection_id}/sensitive-fields",
    response_model=AskDataSensitiveFieldResponse,
    status_code=201,
)
def create_sensitive_field(
    connection_id: UUID, body: AskDataSensitiveFieldInput, db: Database, user: ManageUser
):
    return service.save_sensitive_field(db, user, connection_id, body)


@router.put(
    "/settings/{connection_id}/sensitive-fields/{identifier}",
    response_model=AskDataSensitiveFieldResponse,
)
def update_sensitive_field(
    connection_id: UUID,
    identifier: UUID,
    body: AskDataSensitiveFieldInput,
    db: Database,
    user: ManageUser,
):
    return service.save_sensitive_field(db, user, connection_id, body, identifier)


@router.delete("/settings/{connection_id}/sensitive-fields/{identifier}", status_code=204)
def delete_sensitive_field(
    connection_id: UUID, identifier: UUID, db: Database, user: ManageUser
):
    service.delete_sensitive_field(db, user, connection_id, identifier)


# ---------------------------------------------------------------------------
# Supplementary definitions
# ---------------------------------------------------------------------------


@router.get(
    "/settings/{connection_id}/supplements", response_model=AskDataSupplementPage
)
def supplements(connection_id: UUID, db: Database, user: ReadUser):
    rows = service.supplements(db, user, connection_id)
    return AskDataSupplementPage(
        items=[AskDataSupplementResponse.model_validate(row) for row in rows],
        total=len(rows),
    )


@router.post(
    "/settings/{connection_id}/supplements",
    response_model=AskDataSupplementResponse,
    status_code=201,
)
def create_supplement(
    connection_id: UUID, body: AskDataSupplementInput, db: Database, user: ManageUser
):
    return service.save_supplement(db, user, connection_id, body)


@router.put(
    "/settings/{connection_id}/supplements/{identifier}",
    response_model=AskDataSupplementResponse,
)
def update_supplement(
    connection_id: UUID,
    identifier: UUID,
    body: AskDataSupplementInput,
    db: Database,
    user: ManageUser,
):
    return service.save_supplement(db, user, connection_id, body, identifier)


@router.delete("/settings/{connection_id}/supplements/{identifier}", status_code=204)
def delete_supplement(
    connection_id: UUID, identifier: UUID, db: Database, user: ManageUser
):
    service.delete_supplement(db, user, connection_id, identifier)
