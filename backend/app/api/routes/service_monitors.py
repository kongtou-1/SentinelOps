from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from pydantic import AwareDatetime

from app.api.dependencies import Database, require_permission
from app.core.config import get_settings
from app.core.service_auth import service_authorization
from app.models import ServiceMonitor, User
from app.schemas.monitoring import (
    MonitorInput,
    MonitorResponse,
    MonitorTestInput,
    MonitorTestResponse,
    ObservabilityResponse,
    ServiceMetric,
    TrafficInput,
)
from app.services.monitoring import service_monitors as service
from app.services.records import active_record

router = APIRouter(prefix="/api/v1", tags=["service-monitoring"])
ReadUser = Annotated[User, Depends(require_permission("resources:read"))]
ManageUser = Annotated[User, Depends(require_permission("resources:manage"))]
ProbeUser = Annotated[User, Depends(require_permission("containers:diagnose"))]


@router.get("/service-monitors", response_model=list[MonitorResponse])
def monitors(db: Database, user: ReadUser, application_id: UUID | None = None):
    return service.list_monitors(db, application_id)


@router.get(
    "/business-services/{application_id}/observability", response_model=ObservabilityResponse
)
def observability(application_id: UUID, db: Database, user: ReadUser):
    return service.observability(db, application_id)


@router.post(
    "/business-services/{application_id}/monitors", response_model=MonitorResponse, status_code=201
)
def create(
    application_id: UUID, body: MonitorInput, request: Request, db: Database, user: ManageUser
):
    return service.configure(db, application_id, body, request.app.state.service_probes)


@router.post("/service-monitors/test", response_model=MonitorTestResponse)
async def test_probe(body: MonitorTestInput, request: Request, db: Database, user: ManageUser):
    async with request.app.state.service_probes.semaphore:
        return await service.test_probe(db, body)


@router.get("/service-monitors/{monitor_id}", response_model=MonitorResponse)
def detail(monitor_id: UUID, db: Database, user: ReadUser):
    return service.response(active_record(db, ServiceMonitor, monitor_id))


@router.put("/service-monitors/{monitor_id}", response_model=MonitorResponse)
def update(monitor_id: UUID, body: MonitorInput, request: Request, db: Database, user: ManageUser):
    runtime = request.app.state.service_probes
    with runtime.exclusive(monitor_id):
        row = active_record(db, ServiceMonitor, monitor_id)
        return service.configure(db, row.application_id, body, runtime, monitor_id)


@router.delete("/service-monitors/{monitor_id}", status_code=204)
def remove(monitor_id: UUID, request: Request, db: Database, user: ManageUser):
    runtime = request.app.state.service_probes
    with runtime.exclusive(monitor_id):
        service.remove(db, monitor_id, runtime)


@router.post("/service-monitors/{monitor_id}/probe", response_model=MonitorResponse)
async def probe(monitor_id: UUID, request: Request, db: Database, user: ProbeUser):
    from app.services.access_control import resource_guard

    await request.app.state.service_probes.run(
        monitor_id,
        authorize=resource_guard(user.id, "containers:diagnose", "service_monitor", monitor_id),
    )
    return service.response(active_record(db, ServiceMonitor, monitor_id))


@router.get("/service-monitors/{monitor_id}/metrics")
def metrics(
    monitor_id: UUID,
    metric: ServiceMetric,
    db: Database,
    user: ReadUser,
    start: AwareDatetime | None = None,
    end: AwareDatetime | None = None,
    step: Annotated[int, Query(ge=1, le=86400)] = 30,
):
    return service.metrics(db, monitor_id, metric, start, end, step)


@router.put("/business-services/{application_id}/traffic")
def traffic(application_id: UUID, body: TrafficInput, db: Database, user: ManageUser):
    return service.configure_traffic(db, application_id, body)


@router.get("/servers/{server_id}/probe-config")
def probe_config(server_id: UUID, db: Database, user: ManageUser):
    return Response(
        service.blackbox_config(db, server_id),
        media_type="application/yaml",
        headers={"Content-Disposition": 'attachment; filename="service-blackbox.yml"'},
    )


@router.get("/monitoring/service-probe-metrics")
def probe_metrics(
    request: Request, db: Database, authorization: Annotated[str | None, Header()] = None
):
    service_authorization(authorization, get_settings().monitoring_token_file)
    return Response(
        request.app.state.service_probes.metrics(db), media_type="text/plain; version=0.0.4"
    )
