"""Public contracts for stage-4 source-code evidence and rule review."""

from typing import Any, Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, Field

from app.schemas.auth import RequestModel


class AskDataCodeRuleInput(RequestModel):
    expression: str | None = Field(default=None, max_length=4000)
    description: str | None = Field(default=None, max_length=2000)
    certified: bool | None = None


class AskDataCodeRuleConflictInput(RequestModel):
    action: Literal["keep_manual", "accept_code"]


class AskDataCodeRuleResponse(BaseModel):
    id: UUID
    connection_id: UUID
    model_version_id: UUID
    source_snapshot_id: UUID | None = None
    git_commit: str | None = None
    svn_revision: str | None = None
    content_hash: str = ""
    language: str
    path: str
    start_line: int
    end_line: int
    snippet_hash: str = ""
    rule_kind: str
    object_key: str
    expression: str
    sql_text: str = ""
    detail: dict = Field(default_factory=dict)
    parse_state: str
    source: str
    fingerprint: str
    certified: bool
    edited_by_name: str = ""
    review_required: bool
    review_note: str = ""
    conflict_with: UUID | None = None
    conflict_state: str
    superseded: bool
    created_at: AwareDatetime
    updated_at: AwareDatetime


class AskDataCodeRuleDetail(AskDataCodeRuleResponse):
    snippet: str = ""


class AskDataCodeRulePage(BaseModel):
    items: list[AskDataCodeRuleResponse]
    total: int
    pending_review: int
    conflicts: int


class AskDataCodeRuleSummary(BaseModel):
    source_snapshot_id: UUID | None = None
    git_commit: str | None = None
    svn_revision: str | None = None
    content_hash: str = ""
    source_origin: str = "none"
    source_label: str = ""
    application_id: UUID | None = None
    environment: str = ""
    rule_count: int = 0
    file_count: int = 0
    static_sql_count: int = 0
    dynamic_count: int = 0
    pending_review: int = 0
    conflicts: int = 0
    languages: dict[str, int] = Field(default_factory=dict)
    gaps: dict[str, Any] = Field(default_factory=dict)


class AskDataSourceSnapshotOption(BaseModel):
    id: UUID
    label: str
    origin: Literal["release", "manual", "reference"] = "reference"
    version: str = ""
    git_commit: str | None = None
    svn_revision: str | None = None
    status: str
    file_count: int = 0
    recommended: bool = False


class AskDataSourceSnapshotPage(BaseModel):
    items: list[AskDataSourceSnapshotOption]
    recommended_id: UUID | None = None
    origin: str = "none"
    label: str = ""
    application_id: UUID | None = None
    environment: str = ""
