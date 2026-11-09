from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response

from app.api.dependencies import Database, require_permission
from app.models import (
    User,
)
from app.schemas.sources import (
    ApplicationInput,
    ApplicationResponse,
    DeploymentEnd,
    DeploymentInput,
    DeploymentResponse,
    ReleaseInput,
    ReleaseResponse,
    SnapshotResponse,
    SyncInput,
)
from app.services.sources import catalog as _service

router = APIRouter(prefix="/api/v1", tags=["application-sources"])

ReadUser = Annotated[User, Depends(require_permission("sources:read"))]

ManageUser = Annotated[User, Depends(require_permission("sources:manage"))]

SyncUser = Annotated[User, Depends(require_permission("sources:sync"))]

Offset = Annotated[int, Query(ge=0)]

Limit = Annotated[int, Query(ge=1, le=100)]


@router.get("/applications", response_model=list[ApplicationResponse])
def applications(
    response: Response,
    db: Database,
    user: ReadUser,
    offset: Offset = 0,
    limit: Limit = 50,
    q: str = "",
):
    return _service.applications(response, db, user, offset, limit, q)


@router.post("/applications", response_model=ApplicationResponse, status_code=201)
def create_application(body: ApplicationInput, db: Database, user: ManageUser):
    return _service.create_application(body, db, user)


@router.get("/applications/{application_id}", response_model=ApplicationResponse)
def application(application_id: UUID, db: Database, user: ReadUser):
    return _service.application(application_id, db, user)


@router.put("/applications/{application_id}", response_model=ApplicationResponse)
def update_application(
    application_id: UUID, body: ApplicationInput, db: Database, user: ManageUser
):
    return _service.update_application(application_id, body, db, user)


@router.delete("/applications/{application_id}", status_code=204)
def delete_application(application_id: UUID, db: Database, user: ManageUser):
    return _service.delete_application(application_id, db, user)


@router.post(
    "/applications/{application_id}/source-syncs", response_model=SnapshotResponse, status_code=202
)
def synchronize_source(application_id: UUID, body: SyncInput, db: Database, user: SyncUser):
    return _service.synchronize_source(application_id, body, db, user)


@router.get("/applications/{application_id}/snapshots", response_model=list[SnapshotResponse])
def snapshots(
    application_id: UUID,
    response: Response,
    db: Database,
    user: ReadUser,
    offset: Offset = 0,
    limit: Limit = 50,
):
    return _service.snapshots(application_id, response, db, user, offset, limit)


@router.get("/source-snapshots/{snapshot_id}", response_model=SnapshotResponse)
def snapshot(snapshot_id: UUID, db: Database, user: ReadUser):
    return _service.snapshot(snapshot_id, db, user)


@router.get("/source-snapshots/{snapshot_id}/tree")
def source_tree(
    snapshot_id: UUID,
    db: Database,
    user: ReadUser,
    path: str = ".",
    offset: Offset = 0,
    limit: Limit = 100,
):
    return _service.source_tree(snapshot_id, db, user, path, offset, limit)


@router.get("/source-snapshots/{snapshot_id}/file")
def source_file(
    snapshot_id: UUID,
    db: Database,
    user: ReadUser,
    path: str,
    start_line: Annotated[int, Query(ge=1)] = 1,
    end_line: Annotated[int, Query(ge=1)] = 200,
):
    return _service.source_file(snapshot_id, db, user, path, start_line, end_line)


@router.get("/source-snapshots/{snapshot_id}/search")
def source_search(
    snapshot_id: UUID,
    db: Database,
    user: ReadUser,
    q: Annotated[str, Query(min_length=1, max_length=500)],
    path: str = ".",
):
    return _service.source_search(snapshot_id, db, user, q, path)


@router.get("/source-snapshots/{snapshot_id}/diff")
def source_diff(
    snapshot_id: UUID, other_id: UUID, db: Database, user: ReadUser, path: str | None = None
):
    return _service.source_diff(snapshot_id, other_id, db, user, path)


@router.get("/applications/{application_id}/resolve-source-path")
def resolve_source_path(application_id: UUID, runtime_path: str, db: Database, user: ReadUser):
    return _service.resolve_source_path(application_id, runtime_path, db, user)


@router.post(
    "/applications/{application_id}/releases", response_model=ReleaseResponse, status_code=201
)
def create_release(application_id: UUID, body: ReleaseInput, db: Database, user: ManageUser):
    return _service.create_release(application_id, body, db, user)


@router.get("/applications/{application_id}/releases", response_model=list[ReleaseResponse])
def releases(
    application_id: UUID,
    response: Response,
    db: Database,
    user: ReadUser,
    offset: Offset = 0,
    limit: Limit = 50,
):
    return _service.releases(application_id, response, db, user, offset, limit)


@router.post("/application-releases/{release_id}/attach-snapshot", response_model=ReleaseResponse)
def attach_snapshot(release_id: UUID, snapshot_id: UUID, db: Database, user: ManageUser):
    return _service.attach_snapshot(release_id, snapshot_id, db, user)


@router.post(
    "/applications/{application_id}/deployments", response_model=DeploymentResponse, status_code=201
)
def create_deployment(application_id: UUID, body: DeploymentInput, db: Database, user: ManageUser):
    return _service.create_deployment(application_id, body, db, user)


@router.get("/applications/{application_id}/deployments", response_model=list[DeploymentResponse])
def deployments(
    application_id: UUID,
    response: Response,
    db: Database,
    user: ReadUser,
    offset: Offset = 0,
    limit: Limit = 50,
):
    return _service.deployments(application_id, response, db, user, offset, limit)


@router.post("/application-deployments/{deployment_id}/end", response_model=DeploymentResponse)
def end_deployment(deployment_id: UUID, body: DeploymentEnd, db: Database, user: ManageUser):
    return _service.end_deployment(deployment_id, body, db, user)


@router.get("/containers/{container_id}/deployments")
def container_deployments(container_id: UUID, db: Database, user: ReadUser):
    return _service.container_deployments(container_id, db, user)
