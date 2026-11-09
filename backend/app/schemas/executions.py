from uuid import UUID

from pydantic import AwareDatetime, Field

from app.schemas.auth import RequestModel
from app.schemas.resources import ORMResponse


class ApprovalInput(RequestModel):
    analysis_id: UUID


class DecisionInput(RequestModel):
    comment: str = Field(min_length=1, max_length=2000, pattern=r"\S")


class ExecuteInput(RequestModel):
    pass


class ApprovalResponse(ORMResponse):
    id: UUID
    incident_id: UUID
    analysis_id: UUID
    container_id: UUID
    requested_by: UUID
    approver_id: UUID | None
    status: str
    binding: dict
    binding_hash: str
    expires_at: AwareDatetime
    decided_at: AwareDatetime | None
    decision_comment: str | None
    error_code: str | None
    created_at: AwareDatetime
    updated_at: AwareDatetime


class ExecutionResponse(ORMResponse):
    id: UUID
    approval_id: UUID
    incident_id: UUID
    container_id: UUID
    initiated_by: UUID
    status: str
    binding_hash: str
    started_at: AwareDatetime | None
    dispatched_at: AwareDatetime | None
    finished_at: AwareDatetime | None
    precheck: dict
    output: dict
    verification: dict
    error_code: str | None
    created_at: AwareDatetime
    updated_at: AwareDatetime


class AuditResponse(ORMResponse):
    id: UUID
    user_id: UUID | None
    actor_username: str | None = None
    incident_id: UUID
    incident_title: str | None = None
    approval_id: UUID | None
    report_id: UUID | None
    execution_id: UUID | None
    action: str
    request_id: str
    ip: str | None
    detail: dict
    created_at: AwareDatetime
    updated_at: AwareDatetime
