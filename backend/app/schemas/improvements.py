from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, Field

from app.schemas.auth import RequestModel
from app.schemas.resources import ORMResponse


class ImportImprovements(RequestModel):
    expected_revision: int = Field(ge=1)
    item_indexes: list[int] = Field(min_length=1, max_length=30)


class ImprovementUpdate(RequestModel):
    expected_version: int = Field(ge=1)
    assignee_id: UUID | None = None
    due_at: AwareDatetime | None = None
    acceptance: str = Field(default="", max_length=8000)
    status: Literal["unassigned", "pending", "in_progress", "cancelled"]


class ImprovementSubmit(RequestModel):
    expected_version: int = Field(ge=1)
    result: str = Field(min_length=1, max_length=16000)
    handoff_ids: list[UUID] = Field(default=[], max_length=20)


class ImprovementDecision(RequestModel):
    expected_version: int = Field(ge=1)
    accepted: bool
    evidence: str = Field(min_length=1, max_length=8000)


class ImprovementResponse(ORMResponse):
    id: UUID
    report_id: UUID
    report_revision: int
    item_index: int
    incident_id: UUID
    item: dict
    title: str
    status: str
    assignee_id: UUID | None
    requested_by: UUID
    due_at: AwareDatetime | None
    acceptance: str
    submission: dict
    decision: dict
    version: int
    created_at: AwareDatetime
    updated_at: AwareDatetime


class KnowledgeValidity(RequestModel):
    expected_version: int = Field(ge=1)
    application_ids: list[UUID] = Field(default=[], max_length=100)
    applicable_versions: list[str] = Field(default=[], max_length=50)
    owner_id: UUID | None = None
    review_at: AwareDatetime | None = None
    expires_at: AwareDatetime | None = None


class KnowledgeDraft(RequestModel):
    expected_revision: int = Field(ge=1)
    title: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1, max_length=100000)
