"""Public contracts for the Chinese ask-data conversation loop (stage 3)."""

from typing import Any
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, Field

from app.schemas.auth import RequestModel
from app.schemas.resources import ORMResponse


class AskDataConversationInput(RequestModel):
    connection_id: UUID
    title: str = Field(default="新问数", max_length=200)


class AskDataConversationResponse(ORMResponse):
    id: UUID
    connection_id: UUID
    owner_id: UUID
    title: str
    created_at: AwareDatetime
    updated_at: AwareDatetime


class AskDataRunSummary(ORMResponse):
    id: UUID
    conversation_id: UUID
    status: str
    question: str
    error_code: str | None
    created_at: AwareDatetime
    updated_at: AwareDatetime


class AskDataConversationDetail(AskDataConversationResponse):
    runs: list[AskDataRunSummary] = Field(default_factory=list)


class AskDataRunInput(RequestModel):
    question: str = Field(min_length=1, max_length=2000)
    idempotency_key: UUID


class AskDataColumnMeta(BaseModel):
    name: str
    kind: str
    type_code: int | str | None = None


class AskDataChartSpec(BaseModel):
    kind: str = "none"
    x: str | None = None
    y: list[str] = Field(default_factory=list)
    title: str = ""


class AskDataEvidence(BaseModel):
    kind: str
    name: str
    description: str = ""
    certified: bool | None = None
    # Stage-4 code provenance; absent for term / attribute / metric definitions.
    path: str | None = None
    line: int | None = None
    commit: str | None = None
    snapshot_id: str | None = None
    language: str | None = None
    parse_state: str | None = None


class AskDataExecutionContext(BaseModel):
    connection_id: UUID
    application_id: UUID | None = None
    environment: str = ""
    schema_hash: str = ""
    config_version: int = 0
    model_version_id: UUID
    source_snapshot_id: UUID | None = None


class AskDataResult(BaseModel):
    answer: str = ""
    sql: str = ""
    executed_sql: str | None = None
    columns: list[AskDataColumnMeta] = Field(default_factory=list)
    rows: list[list[Any]] = Field(default_factory=list)
    chart: AskDataChartSpec = Field(default_factory=AskDataChartSpec)
    evidence: list[AskDataEvidence] = Field(default_factory=list)
    truncated: bool = False
    query_time: str | None = None
    row_count: int = 0
    clarification: str | None = None
    certification: str = "none"


class AskDataStepEvent(BaseModel):
    seq: int
    step: str
    status: str
    time: AwareDatetime
    summary: str


class AskDataEventPage(BaseModel):
    items: list[AskDataStepEvent]
    latest_seq: int
    terminal: bool


class AskDataRunResponse(ORMResponse):
    id: UUID
    conversation_id: UUID
    connection_id: UUID
    model_version_id: UUID
    status: str
    question: str
    error_code: str | None
    created_at: AwareDatetime
    updated_at: AwareDatetime


class AskDataRunDetail(AskDataRunResponse):
    result: AskDataResult | None = None
    context: AskDataExecutionContext | None = None
    events: list[AskDataStepEvent] = Field(default_factory=list)
