from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Response
from pydantic import AwareDatetime

from app.api.dependencies import Database, require_permission
from app.models import User
from app.schemas.resources import (
    ContainerDiagnostic,
    ContainerHealth,
    ContainerResponse,
    ContainerServiceInput,
    CredentialInput,
    CredentialResponse,
    Metric,
    ServerContainerOverview,
    ServerInput,
    ServerMonitoringSummary,
    ServerResponse,
)
from app.services.resources import crud as _service
from app.services.resources import metrics as _metrics

router = APIRouter(prefix="/api/v1", tags=["resources"])

ReadUser = Annotated[User, Depends(require_permission("resources:read"))]

ManageUser = Annotated[User, Depends(require_permission("resources:manage"))]
EditUser = Annotated[User, Depends(require_permission("resources:edit"))]

CredentialUser = Annotated[User, Depends(require_permission("credentials:manage"))]

SyncUser = Annotated[User, Depends(require_permission("containers:sync"))]

DiagnoseUser = Annotated[User, Depends(require_permission("containers:diagnose"))]


@router.get("/ssh-credentials", response_model=list[CredentialResponse])
def credentials(
    response: Response,
    db: Database,
    user: CredentialUser,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
):
    return _service.credentials(response, db, user, offset, limit)


@router.post("/ssh-credentials", response_model=CredentialResponse, status_code=201)
def create_credential(body: CredentialInput, db: Database, user: CredentialUser):
    return _service.create_credential(body, db, user)


@router.put("/ssh-credentials/{credential_id}", response_model=CredentialResponse)
def replace_credential(
    credential_id: UUID, body: CredentialInput, db: Database, user: CredentialUser
):
    return _service.replace_credential(credential_id, body, db, user)


@router.delete("/ssh-credentials/{credential_id}", status_code=204)
def delete_credential(credential_id: UUID, db: Database, user: CredentialUser):
    return _service.delete_credential(credential_id, db, user)


@router.get("/servers", response_model=list[ServerResponse])
def servers(
    response: Response,
    db: Database,
    user: ReadUser,
    q: Annotated[str | None, Query(max_length=100)] = None,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
):
    return _service.servers(response, db, user, q, offset, limit)


@router.post("/servers", response_model=ServerResponse, status_code=201)
def create_server(body: ServerInput, db: Database, user: ManageUser):
    return _service.create_server(body, db, user)


@router.get("/servers/{server_id}", response_model=ServerResponse)
def server_detail(server_id: UUID, db: Database, user: ReadUser):
    return _service.server_detail(server_id, db, user)


@router.put("/servers/{server_id}", response_model=ServerResponse)
def replace_server(server_id: UUID, body: ServerInput, db: Database, user: ManageUser):
    return _service.replace_server(server_id, body, db, user)


@router.delete("/servers/{server_id}", status_code=204)
def delete_server(server_id: UUID, db: Database, user: ManageUser):
    return _service.delete_server(server_id, db, user)


@router.post("/servers/{server_id}/test-ssh")
def test_ssh(server_id: UUID, db: Database, user: ManageUser):
    return _service.test_ssh(server_id, db, user)


@router.get("/monitoring/targets")
def targets(db: Database, authorization: Annotated[str | None, Header()] = None):
    return _service.targets(db, authorization)


@router.get("/monitoring/servers-summary", response_model=list[ServerMonitoringSummary])
def servers_summary(
    db: Database,
    user: ReadUser,
    server_ids: Annotated[list[UUID], Query(min_length=1, max_length=100)],
):
    return _service.servers_summary(db, user, server_ids)


@router.get("/servers/{server_id}/metrics")
def server_metrics(
    server_id: UUID,
    metric: Metric,
    db: Database,
    user: ReadUser,
    start: AwareDatetime | None = None,
    end: AwareDatetime | None = None,
    step: Annotated[int, Query(ge=15, le=86400)] = 30,
):
    return _metrics.server_metrics(server_id, metric, db, user, start, end, step)


@router.post("/servers/{server_id}/sync-containers", response_model=list[ContainerResponse])
def sync_containers(server_id: UUID, db: Database, user: SyncUser):
    return _service.sync_containers(server_id, db, user)


@router.get("/monitoring/container-metrics")
def inventory_metrics(db: Database, authorization: Annotated[str | None, Header()] = None):
    return _service.inventory_metrics(db, authorization)


@router.get("/containers", response_model=list[ContainerResponse])
def containers(
    response: Response,
    db: Database,
    user: ReadUser,
    server_id: UUID | None = None,
    status: Annotated[str | None, Query(pattern="^(running|stopped)$")] = None,
    q: Annotated[str | None, Query(max_length=100)] = None,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
):
    return _service.containers(response, db, user, server_id, status, q, offset, limit)


@router.get("/containers/{container_id}", response_model=ContainerResponse)
def container_detail(container_id: UUID, db: Database, user: ReadUser):
    return _service.container_detail(container_id, db, user)


@router.put("/containers/{container_id}/service", response_model=ContainerResponse)
def update_container_service(
    container_id: UUID, body: ContainerServiceInput, db: Database, user: EditUser
):
    return _service.update_container_service(container_id, body, db, user)


@router.get("/servers/{server_id}/container-overview", response_model=ServerContainerOverview)
def server_container_overview(server_id: UUID, db: Database, user: ReadUser):
    return _service.server_container_overview(server_id, db, user)


@router.get("/containers/{container_id}/health", response_model=ContainerHealth)
def read_container_health(container_id: UUID, db: Database, user: ReadUser):
    return _service.read_container_health(container_id, db, user)


@router.get("/containers/{container_id}/diagnostics", response_model=ContainerDiagnostic)
def container_diagnostics(
    container_id: UUID,
    db: Database,
    user: DiagnoseUser,
    kind: Literal["state", "logs"] = "state",
):
    return _service.container_diagnostics(container_id, db, user, kind)


@router.delete("/containers/{container_id}", status_code=204)
def retire_container(container_id: UUID, db: Database, user: ManageUser):
    return _service.retire_container(container_id, db, user)


@router.get("/containers/{container_id}/metrics")
def container_metrics(
    container_id: UUID,
    metric: Metric,
    db: Database,
    user: ReadUser,
    start: AwareDatetime | None = None,
    end: AwareDatetime | None = None,
    step: Annotated[int, Query(ge=15, le=86400)] = 30,
):
    return _metrics.container_metrics(container_id, metric, db, user, start, end, step)


@router.get("/servers/{server_id}/collectors-compose")
def collectors_compose(server_id: UUID, db: Database, user: ManageUser):
    return _service.collectors_compose(server_id, db, user)
