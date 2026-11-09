from typing import Annotated, Literal
from uuid import UUID

from pydantic import AwareDatetime, Field, model_validator

from app.schemas.auth import RequestModel
from app.schemas.resources import ORMResponse

Title = Annotated[str, Field(min_length=1, max_length=200, pattern=r"\S")]
Content = Annotated[str, Field(min_length=1, max_length=100000, pattern=r"\S")]
Tags = Annotated[list[Annotated[str, Field(min_length=1, max_length=50)]], Field(max_length=20)]


class DocumentInput(RequestModel):
    title: Title
    content: Content
    source: str = Field(default="", max_length=500)
    tags: Tags = []
    enabled: bool = True


class DocumentResponse(ORMResponse):
    id: UUID
    title: str
    content: str
    source: str
    tags: list[str]
    enabled: bool
    version: int
    index_status: str
    error_code: str | None
    created_at: AwareDatetime
    updated_at: AwareDatetime
    application_ids: list[str]
    applicable_versions: list[str]
    owner_id: UUID | None
    review_at: AwareDatetime | None
    expires_at: AwareDatetime | None


class SkillInput(RequestModel):
    code: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,99}$")
    title: Title
    content: Content
    scripts: dict[str, str] = Field(default_factory=dict, max_length=10)
    resources: dict[str, str] = Field(default_factory=dict, max_length=30)
    tags: Tags = []
    enabled: bool = True

    @model_validator(mode="after")
    def valid_scripts(self):
        import re

        if any(
            not re.fullmatch(r"scripts/[A-Za-z_][A-Za-z0-9_-]*\.py", name)
            or len(code.encode()) > 32768
            for name, code in self.scripts.items()
        ):
            raise ValueError("Use scripts/name.py with at most 32 KiB per script")
        if any(
            not re.fullmatch(r"(?:references|assets)/[A-Za-z0-9_-]+\.(?:md|txt|json|csv)", name)
            or len(value.encode()) > 32768
            for name, value in self.resources.items()
        ):
            raise ValueError("Use bounded text resources under references/ or assets/")
        if (
            sum(
                len(value.encode())
                for value in [self.content, *self.scripts.values(), *self.resources.values()]
            )
            > 512 * 1024
        ):
            raise ValueError("Skill package exceeds 512 KiB")
        return self


class SkillResponse(ORMResponse):
    id: UUID
    code: str
    title: str
    content: str
    scripts: dict[str, str] = Field(default_factory=dict)
    resources: dict[str, str] = Field(default_factory=dict)
    tags: list[str]
    enabled: bool
    version: int
    created_at: AwareDatetime
    updated_at: AwareDatetime
    application_ids: list[str]
    applicable_versions: list[str]
    owner_id: UUID | None
    review_at: AwareDatetime | None
    expires_at: AwareDatetime | None


class AnalyzeInput(RequestModel):
    force: bool = False


class DiagnosisPolicyInput(RequestModel):
    enabled: bool = False


class DiagnosisPolicyResponse(DiagnosisPolicyInput):
    configured_by: UUID | None = None
    updated_at: AwareDatetime | None = None
    ai_available: bool = False


class ProposalParameters(RequestModel):
    container_id: UUID


class Proposal(RequestModel):
    skill: Literal["restart_container"]
    version: Literal["1"] = "1"
    parameters: ProposalParameters
    risk: Literal["medium"] = "medium"
    rationale: str = Field(min_length=1, max_length=2000)
    impact: str = Field(min_length=1, max_length=2000)
    executable: Literal[False] = False
    requires_human_approval: Literal[True] = True


class Cause(RequestModel):
    description: str = Field(min_length=1, max_length=2000)
    evidence_ids: list[str] = Field(min_length=1, max_length=30)


class Diagnosis(RequestModel):
    summary: str = Field(min_length=1, max_length=2000)
    possible_causes: list[Cause] = Field(max_length=10)
    impact: str = Field(min_length=1, max_length=2000)
    evidence_ids: list[str] = Field(min_length=1, max_length=30)
    knowledge_ids: list[str] = Field(max_length=10)
    suggested_checks: list[str] = Field(max_length=10)
    data_gaps: list[str] = Field(max_length=30)
    proposal: Proposal | None = None
    requires_human_approval: Literal[True] = True


class AnalysisResponse(ORMResponse):
    id: UUID
    incident_id: UUID
    requested_by: UUID
    trigger: Literal["manual", "automatic"]
    status: str
    config: dict
    source_context: dict
    database_context: dict
    session_id: str | None
    started_at: AwareDatetime | None
    finished_at: AwareDatetime | None
    error_code: str | None
    evidence: list
    knowledge: list
    tool_calls: list
    result: dict
    created_at: AwareDatetime
    updated_at: AwareDatetime
