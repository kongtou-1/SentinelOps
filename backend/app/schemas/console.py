from uuid import UUID

from pydantic import AwareDatetime, BaseModel

from app.schemas.reports import ReportResourceContext


class ConsoleItem(BaseModel):
    id: UUID
    incident_id: UUID
    incident_title: str
    severity: str
    status: str
    actor_id: UUID
    actor_username: str
    created_at: AwareDatetime
    finished_at: AwareDatetime | None = None
    expires_at: AwareDatetime | None = None
    container_id: UUID | None = None
    container_name: str | None = None
    action: str | None = None
    revision: int | None = None
    error_code: str | None = None
    resource_context: ReportResourceContext | None = None


class DashboardSummary(BaseModel):
    servers_total: int
    servers_monitored: int
    containers_total: int
    containers_by_status: dict[str, int]
    containers_last_seen_at: AwareDatetime | None
    llm_total: int
    llm_by_status: dict[str, int]
    approvals_pending: int
    alerts_by_status: dict[str, int]
    alerts_unacknowledged: int
    incidents_by_status: dict[str, int]
