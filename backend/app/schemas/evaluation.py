from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, Field

from app.schemas.auth import RequestModel
from app.schemas.resources import ORMResponse

Verdict = Literal["correct", "partial", "incorrect", "inconclusive"]


class ReviewInput(RequestModel):
    verdict: Verdict
    confirmed_cause: str = Field(default="", max_length=4000)
    evidence: str = Field(min_length=1, max_length=8000)


class ReviewResponse(ReviewInput, ORMResponse):
    id: UUID
    analysis_id: UUID
    reviewer_id: UUID
    created_at: AwareDatetime


class CaseChecks(RequestModel):
    required_tools: list[str] = Field(default=[], max_length=30)
    cause_terms: list[str] = Field(default=[], max_length=20)


class CaseInput(RequestModel):
    analysis_id: UUID
    title: str = Field(min_length=1, max_length=200)
    previous_id: UUID | None = None
    checks: CaseChecks
    human_label: str = Field(min_length=1, max_length=4000)
    confirm_sanitized: Literal[True]
    expected_snapshot_hash: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")


class CasePreview(RequestModel):
    analysis_id: UUID
    snapshot: dict
    content_hash: str


class CaseResponse(ORMResponse):
    id: UUID
    family_id: UUID
    version: int
    title: str
    analysis_id: UUID
    created_by: UUID
    snapshot: dict
    content_hash: str
    checks: CaseChecks
    human_label: str
    created_at: AwareDatetime


class ReplayInput(RequestModel):
    idempotency_key: UUID
    provider_kind: Literal["mock", "model"] = "mock"
    confirm_paid: bool = False
    skill_ids: list[UUID] = Field(default=[], max_length=10)
    prompt: str = Field(
        default="根据冻结案例进行只读诊断，先加载 Skill 并引用证据，输出诊断 JSON。",
        min_length=1,
        max_length=8000,
    )


class ReplayResponse(ORMResponse):
    id: UUID
    case_id: UUID
    requested_by: UUID
    provider_kind: str
    status: str
    config: dict
    prompt: str
    result: dict
    review: dict
    version: int
    started_at: AwareDatetime | None
    finished_at: AwareDatetime | None
    error_code: str | None
    created_at: AwareDatetime


class ReplayReview(ReviewInput):
    expected_version: int = Field(ge=1)


class OutcomeResponse(ORMResponse):
    id: UUID
    result_id: UUID
    horizon_hours: int
    status: str
    observation: dict
    incident_links: list
    version: int
    created_at: AwareDatetime


class OutcomeLink(RequestModel):
    incident_id: UUID
    expected_version: int = Field(ge=1)
    evidence: str = Field(min_length=1, max_length=4000)
