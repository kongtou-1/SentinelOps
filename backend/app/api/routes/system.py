from typing import Annotated

from fastapi import APIRouter, Depends, Header, Response

from app.api.dependencies import Database, require_permission
from app.models import User
from app.schemas.diagnosis import DiagnosisPolicyInput, DiagnosisPolicyResponse
from app.services import system as _service
from app.services.diagnosis import policy

router = APIRouter()


@router.get("/metrics", tags=["monitoring"])
def platform_metrics(db: Database, authorization: Annotated[str | None, Header()] = None):
    from app.core.config import get_settings
    from app.core.service_auth import service_authorization
    from app.services.operations.telemetry import metrics

    service_authorization(authorization, get_settings().monitoring_token_file)
    return Response(metrics(db), media_type="text/plain; version=0.0.4")


@router.get("/api/v1/system/diagnosis-policy", response_model=DiagnosisPolicyResponse)
def diagnosis_policy(
    db: Database,
    user: Annotated[User, Depends(require_permission("system:read"))],
):
    return policy.read(db, user)


@router.put("/api/v1/system/diagnosis-policy", response_model=DiagnosisPolicyResponse)
def update_diagnosis_policy(
    body: DiagnosisPolicyInput,
    db: Database,
    user: Annotated[User, Depends(require_permission("rules:manage"))],
):
    return policy.update(db, body, user)


@router.get("/health/live", tags=["health"])
def live():
    return _service.live()


@router.get("/health/ready", tags=["health"])
def ready(db: Database):
    return _service.ready(db)


@router.get("/api/v1/system/status", tags=["system"])
def system_status(
    db: Database,
    user: Annotated[User, Depends(require_permission("system:read"))],
):
    return _service.system_status(db, user)
