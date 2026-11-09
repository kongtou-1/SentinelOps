from typing import Annotated, Literal
from uuid import UUID

from email_validator import EmailNotValidError, validate_email
from pydantic import AwareDatetime, Field, field_validator

from app.schemas.auth import RequestModel
from app.schemas.resources import ORMResponse

Text = Annotated[str, Field(min_length=1, max_length=4000, pattern=r"\S")]
Comment = Annotated[str, Field(min_length=1, max_length=2000, pattern=r"\S")]
References = Annotated[list[str], Field(max_length=100)]


class ReportCause(RequestModel):
    description: Text
    confidence: Literal["confirmed", "suspected", "unknown"]
    evidence_ids: References


class Improvement(RequestModel):
    action: Text
    owner: str = Field(default="待分配", max_length=200)
    deadline: str = Field(default="待确定", max_length=100)


class ReportContent(RequestModel):
    summary: Text
    impact: Text
    causes: list[ReportCause] = Field(max_length=20)
    response_and_recovery: Text
    lessons: Annotated[list[Text], Field(max_length=20)]
    improvements: list[Improvement] = Field(max_length=30)
    data_gaps: Annotated[list[Text], Field(max_length=30)]
    evidence_ids: References


class GenerateReport(RequestModel):
    force: bool = False


class EditReport(RequestModel):
    expected_revision: int = Field(ge=1)
    content: ReportContent


class ReviewReport(RequestModel):
    expected_revision: int = Field(ge=1)
    decision: Literal["approve", "request_changes"]
    comment: Comment


class SendReport(RequestModel):
    expected_revision: int = Field(ge=1)
    recipients: list[str] = Field(min_length=1, max_length=20)
    comment: Comment
    idempotency_key: UUID

    @field_validator("recipients")
    @classmethod
    def valid_recipients(cls, value):
        normalized = []
        for address in value:
            try:
                email = validate_email(
                    address, check_deliverability=False, test_environment=True
                ).ascii_email
            except EmailNotValidError as exc:
                raise ValueError("Invalid email recipient") from exc
            if not email or len(email) > 254:
                raise ValueError("Only ASCII email addresses are supported")
            normalized.append(email)
        return sorted(set(normalized))


class ReportResource(ORMResponse):
    id: UUID
    name: str


class ReportTarget(ReportResource):
    kind: Literal["server", "container", "llm_endpoint"]


class ReportResourceContext(ORMResponse):
    services: list[ReportResource] = Field(default_factory=list)
    resources: list[ReportTarget] = Field(default_factory=list)
    source: Literal["frozen", "current_assets"] = "current_assets"


class ReportResponse(ORMResponse):
    id: UUID
    incident_id: UUID
    resource_context: ReportResourceContext = Field(default_factory=ReportResourceContext)
    requested_by: UUID
    status: str
    revision: int
    snapshot: dict
    snapshot_hash: str
    config: dict
    tool_calls: list
    content: dict | None = None
    started_at: AwareDatetime | None
    finished_at: AwareDatetime | None
    error_code: str | None
    reviewed_by: UUID | None
    reviewed_at: AwareDatetime | None
    review_comment: str | None
    created_at: AwareDatetime
    updated_at: AwareDatetime


class RevisionResponse(ORMResponse):
    id: UUID
    report_id: UUID
    revision: int
    author_id: UUID | None
    content: dict
    content_hash: str
    created_at: AwareDatetime
    updated_at: AwareDatetime


class DeliveryResponse(ORMResponse):
    id: UUID
    report_id: UUID
    revision: int
    requested_by: UUID
    idempotency_key: UUID
    recipients: list[str]
    sender: str
    subject: str
    comment: str
    attachment_hash: str
    status: str
    dispatched_at: AwareDatetime | None
    finished_at: AwareDatetime | None
    error_code: str | None
    created_at: AwareDatetime
    updated_at: AwareDatetime
