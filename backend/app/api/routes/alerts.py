from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Response
from pydantic import AwareDatetime

from app.api.dependencies import Database, require_permission
from app.models import (
    User,
)
from app.schemas.resources import (
    AcknowledgeInput,
    AlertResponse,
    RuleInput,
    RuleResponse,
    WebhookPayload,
)
from app.services import alerts as _service

router = APIRouter(prefix="/api/v1", tags=["alerts"])

RuleUser = Annotated[User, Depends(require_permission("rules:manage"))]

ReadUser = Annotated[User, Depends(require_permission("alerts:read"))]

AckUser = Annotated[User, Depends(require_permission("alerts:ack"))]


@router.get("/alert-rules", response_model=list[RuleResponse])
def rules(
    response: Response,
    db: Database,
    user: ReadUser,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
):
    return _service.rules(response, db, user, offset, limit)


@router.post("/alert-rules", response_model=RuleResponse, status_code=201)
def create_rule(body: RuleInput, db: Database, user: RuleUser):
    return _service.create_rule(body, db, user)


@router.put("/alert-rules/{rule_id}", response_model=RuleResponse)
def replace_rule(rule_id: UUID, body: RuleInput, db: Database, user: RuleUser):
    return _service.replace_rule(rule_id, body, db, user)


@router.delete("/alert-rules/{rule_id}", status_code=204)
def delete_rule(rule_id: UUID, db: Database, user: RuleUser):
    return _service.delete_rule(rule_id, db, user)


@router.post("/monitoring/apply-rules")
def synchronize_rules(db: Database, user: RuleUser):
    return _service.synchronize_rules(db, user)


@router.post("/hooks/alertmanager")
def webhook(
    body: WebhookPayload, db: Database, authorization: Annotated[str | None, Header()] = None
):
    return _service.webhook(body, db, authorization)


@router.get("/alerts", response_model=list[AlertResponse])
def alerts(
    response: Response,
    db: Database,
    user: ReadUser,
    status: Annotated[str | None, Query(pattern="^(firing|resolved)$")] = None,
    server_id: UUID | None = None,
    llm_endpoint_id: UUID | None = None,
    prediction_task_id: UUID | None = None,
    incident_id: UUID | None = None,
    severity: Annotated[str | None, Query(pattern="^(warning|critical)$")] = None,
    acknowledged: bool | None = None,
    start: AwareDatetime | None = None,
    end: AwareDatetime | None = None,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
):
    return _service.alerts(
        response,
        db,
        user,
        status,
        server_id,
        llm_endpoint_id,
        prediction_task_id,
        incident_id,
        severity,
        acknowledged,
        start,
        end,
        offset,
        limit,
    )


@router.get("/alerts/{alert_id}", response_model=AlertResponse)
def alert_detail(alert_id: UUID, db: Database, user: ReadUser):
    return _service.alert_detail(alert_id, db, user)


@router.post("/alerts/{alert_id}/ack", response_model=AlertResponse)
def acknowledge(alert_id: UUID, body: AcknowledgeInput, db: Database, user: AckUser):
    return _service.acknowledge(alert_id, body, db, user)
