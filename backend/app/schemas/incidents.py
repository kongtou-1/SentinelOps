from typing import Annotated, Literal
from uuid import UUID

from pydantic import AwareDatetime, Field, model_validator

from app.schemas.auth import RequestModel
from app.schemas.resources import ORMResponse

IncidentStatus = Literal["open", "in_progress", "recovered", "closed", "merged"]
Title = Annotated[str, Field(min_length=1, max_length=200, pattern=r"\S")]
Comment = Annotated[str, Field(min_length=1, max_length=2000, pattern=r"\S")]


class IncidentResponse(ORMResponse):
    id: UUID
    title: str
    severity: Literal["warning", "critical"]
    status: IncidentStatus
    assignee_id: UUID | None
    started_at: AwareDatetime
    last_alert_started_at: AwareDatetime
    recovered_at: AwareDatetime | None
    closed_at: AwareDatetime | None
    closure: dict = Field(default_factory=dict)
    merged_into_id: UUID | None
    created_at: AwareDatetime
    updated_at: AwareDatetime


class TimelineResponse(ORMResponse):
    id: UUID
    incident_id: UUID
    event_type: str
    occurred_at: AwareDatetime
    actor_type: str
    actor_id: UUID | None
    content: dict
    created_at: AwareDatetime
    updated_at: AwareDatetime


class IncidentUpdate(RequestModel):
    title: Title | None = None
    assignee_id: UUID | None = None

    @model_validator(mode="after")
    def valid_update(self):
        if not self.model_fields_set or ("title" in self.model_fields_set and self.title is None):
            raise ValueError("Provide title or assignee_id; title cannot be null")
        return self


class StatusInput(RequestModel):
    status: Literal["open", "in_progress", "closed"]
    comment: Comment
    force: bool = False

    @model_validator(mode="after")
    def validate_force(self):
        if self.force and self.status != "closed":
            raise ValueError("Force is only supported when closing an incident")
        self.comment = self.comment.strip()
        return self


class CommentInput(RequestModel):
    comment: Comment


class MergeInput(CommentInput):
    target_incident_id: UUID


class AssigneeResponse(ORMResponse):
    id: UUID
    username: str
    role: Literal["operator", "admin"]


class IncidentMonitor(ORMResponse):
    id: UUID
    application_id: UUID
    name: str


class IncidentDetail(IncidentResponse):
    service_monitors: list[IncidentMonitor] = Field(default_factory=list)
    server_ids: list[UUID]
    llm_endpoint_ids: list[UUID]
    alert_count: int
    firing_alert_count: int
    recovery_pending_alert_count: int
    recovery_terminated_alert_count: int
    recovery_verified_alert_count: int
    timeline: list[TimelineResponse]
