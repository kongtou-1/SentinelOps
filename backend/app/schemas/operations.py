from typing import Annotated, Literal
from uuid import UUID

from pydantic import AwareDatetime, Field, model_validator

from app.schemas.auth import RequestModel
from app.schemas.notifications import ResponsePolicyInput
from app.schemas.resources import ORMResponse

Operation = Annotated[str, Field(max_length=100, pattern=r"^(?:[a-z][a-z0-9_.-]*)?$")]
ObjectiveKind = Literal["business_success", "probe_availability", "request_p95", "llm_ttft_p95"]


class ObjectiveInput(RequestModel):
    name: str = Field(min_length=1, max_length=100, pattern=r"\S")
    kind: ObjectiveKind
    monitor_id: UUID | None = None
    operation: Operation = ""
    metric_source: Literal["application", "platform"] = "application"
    target: float = Field(gt=0, le=3600, allow_inf_nan=False)
    window_days: Literal[1, 7, 30] = 7
    enabled: bool = True
    expected_version: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def bindings(self):
        if self.metric_source == "platform":
            allowed = {
                "business_success": ("diagnosis", "report", "execution", "source_snapshot"),
                "llm_ttft_p95": ("diagnosis", "report", "evaluation", "probe"),
            }
            if self.operation not in allowed.get(self.kind, ()):
                raise ValueError("Choose a supported platform workflow")
        if self.kind in ("probe_availability", "request_p95") and not self.monitor_id:
            raise ValueError("Choose a monitor")
        if self.kind in ("business_success", "llm_ttft_p95") and not self.operation:
            raise ValueError("Choose a business operation")
        if self.kind in ("business_success", "probe_availability") and self.target > 1:
            raise ValueError("Ratio target must be in (0,1]")
        return self


class ObjectiveResponse(ORMResponse):
    id: UUID
    application_id: UUID
    monitor_id: UUID | None
    name: str
    kind: ObjectiveKind
    operation: str
    metric_source: str
    target: float
    window_days: int
    enabled: bool
    version: int
    evaluation: dict
    checked_at: AwareDatetime | None
    created_at: AwareDatetime
    updated_at: AwareDatetime


class OperationsPolicyInput(RequestModel):
    expected_version: int = Field(ge=0)
    recovery_seconds: int = Field(default=180, ge=30, le=1800)
    response: ResponsePolicyInput | None = None


class OperationsPolicyResponse(RequestModel):
    version: int
    recovery_seconds: int
    response: ResponsePolicyInput = Field(default_factory=ResponsePolicyInput)


class RelationDecision(RequestModel):
    expected_version: int = Field(ge=1)
    status: Literal["confirmed", "dismissed"]
    comment: str = Field(min_length=1, max_length=2000, pattern=r"\S")


class RelationResponse(ORMResponse):
    id: UUID
    incident_id: UUID
    related_incident_id: UUID
    kind: str
    status: str
    evidence: dict
    decided_by: UUID | None
    version: int
    created_at: AwareDatetime
    updated_at: AwareDatetime
