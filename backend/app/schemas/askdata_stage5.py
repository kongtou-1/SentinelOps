"""Stage-5 request and response contracts for ask-data governance."""

from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, Field, SecretStr

from app.schemas.askdata_runs import AskDataResult
from app.schemas.auth import RequestModel
from app.schemas.resources import ORMResponse

MAX_SQL = 200_000


class AskDataAnalysisCreate(RequestModel):
    connection_id: UUID
    name: str = Field(min_length=1, max_length=200)
    category: str = Field(default="", max_length=60)
    question: str = Field(min_length=1, max_length=2000)
    sql_text: str = Field(min_length=1, max_length=MAX_SQL)
    explanation: str = Field(default="", max_length=4000)
    chart: dict = Field(default_factory=dict)
    evidence: list = Field(default_factory=list)
    model_version_id: UUID | None = None
    source_run_id: UUID | None = None


class AskDataAnalysisUpdate(RequestModel):
    name: str = Field(min_length=1, max_length=200)
    category: str = Field(default="", max_length=60)
    question: str = Field(min_length=1, max_length=2000)
    sql_text: str = Field(min_length=1, max_length=MAX_SQL)
    explanation: str = Field(default="", max_length=4000)
    chart: dict = Field(default_factory=dict)
    evidence: list = Field(default_factory=list)


class AskDataAnalysisResponse(ORMResponse):
    id: UUID
    connection_id: UUID
    model_version_id: UUID | None
    owner_id: UUID
    name: str
    category: str
    question: str
    sql_text: str
    explanation: str
    chart: dict
    evidence: list
    source_run_id: UUID | None
    last_status: str
    last_error_code: str | None
    last_row_count: int | None
    last_duration_ms: int | None
    last_executed_at: AwareDatetime | None
    created_at: AwareDatetime
    updated_at: AwareDatetime


class AskDataAnalysisPage(BaseModel):
    items: list[AskDataAnalysisResponse]
    total: int
    categories: list[str]


class AskDataAnalysisRunResponse(BaseModel):
    status: str
    error_code: str | None = None
    duration_ms: int
    executed_at: AwareDatetime
    model_version_id: UUID | None
    result: AskDataResult | None = None


class AskDataChannelOption(BaseModel):
    id: UUID
    model: str
    base_url: str
    enabled: bool


class AskDataExecutionSettingsInput(RequestModel):
    expected_version: int = Field(ge=0)
    budget_seconds: int = Field(default=180, ge=30, le=900)
    reasoning_channel_id: UUID | None = None
    light_channel_id: UUID | None = None
    vector_model: str = Field(default="", max_length=120)
    vector_dimension: int = Field(default=512, ge=1, le=8192)
    reranker_enabled: bool = False
    reranker_endpoint: str = Field(default="", max_length=300)
    reranker_model: str = Field(default="", max_length=120)
    reranker_api_key: SecretStr | None = None


class AskDataExecutionSettingsResponse(BaseModel):
    connection_id: UUID
    version: int
    budget_seconds: int
    reasoning_channel_id: UUID | None
    light_channel_id: UUID | None
    channels: list[AskDataChannelOption]
    vector_model: str
    vector_dimension: int
    vector_signature: str
    reranker_enabled: bool
    reranker_endpoint: str
    reranker_model: str
    reranker_configured: bool
    reranker_verified_at: AwareDatetime | None
    updated_at: AwareDatetime | None


class AskDataConnectionTestResponse(BaseModel):
    ok: bool
    message: str
    latency_ms: int | None = None
    tested_at: AwareDatetime


class AskDataZoneInput(RequestModel):
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=300)
    targets: list[dict] = Field(default_factory=list, max_length=500)
    enabled: bool = True


class AskDataZoneResponse(ORMResponse):
    id: UUID
    connection_id: UUID
    name: str
    description: str
    targets: list
    enabled: bool
    updated_at: AwareDatetime


class AskDataTagTargetInput(RequestModel):
    object_kind: Literal["table", "column", "term", "metric", "code_rule"]
    object_key: str = Field(min_length=1, max_length=300)


class AskDataTagInput(RequestModel):
    name: str = Field(min_length=1, max_length=60)
    color: str = Field(default="", max_length=20)
    description: str = Field(default="", max_length=300)
    targets: list[AskDataTagTargetInput] = Field(default_factory=list, max_length=500)


class AskDataTagResponse(ORMResponse):
    id: UUID
    connection_id: UUID
    name: str
    color: str
    description: str
    targets: list[AskDataTagTargetInput]
    updated_at: AwareDatetime


class AskDataRuleInput(RequestModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=300)
    kind: Literal[
        "statistical_scope",
        "time_semantics",
        "unit",
        "filter",
        "soft_delete",
        "status",
    ]
    target: str = Field(default="", max_length=300)
    expression: str = Field(default="", max_length=2000)
    unit: str = Field(default="", max_length=30)
    priority: int = Field(default=100, ge=0, le=1000)
    enabled: bool = True


class AskDataRuleResponse(ORMResponse):
    id: UUID
    connection_id: UUID
    name: str
    description: str
    kind: str
    target: str
    expression: str
    unit: str
    priority: int
    enabled: bool
    updated_at: AwareDatetime


class AskDataSensitiveFieldInput(RequestModel):
    object_key: str = Field(min_length=3, max_length=300)
    category: Literal[
        "mobile", "id_card", "bank_card", "email", "address", "name", "amount",
        "credential", "other",
    ] = "other"
    policy: Literal["mask", "exclude"] = "mask"
    mask_strategy: Literal["keep_prefix", "keep_suffix", "hash", "full"] = "full"
    mask_argument: int = Field(default=0, ge=0, le=32)
    sampling: Literal["allow", "exclude"] = "exclude"
    note: str = Field(default="", max_length=300)
    enabled: bool = True


class AskDataSensitiveFieldResponse(ORMResponse):
    id: UUID
    connection_id: UUID
    object_key: str
    category: str
    policy: str
    mask_strategy: str
    mask_argument: int
    sampling: str
    note: str
    enabled: bool
    updated_at: AwareDatetime


class AskDataSupplementInput(RequestModel):
    kind: Literal["abbreviation", "alias", "context"] = "alias"
    term: str = Field(min_length=1, max_length=120)
    definition: str = Field(default="", max_length=2000)
    aliases: list[str] = Field(default_factory=list, max_length=50)
    applies_to: str = Field(default="", max_length=300)
    enabled: bool = True


class AskDataSupplementResponse(ORMResponse):
    id: UUID
    connection_id: UUID
    kind: str
    term: str
    definition: str
    aliases: list
    applies_to: str
    enabled: bool
    updated_at: AwareDatetime


class AskDataZonePage(BaseModel):
    items: list[AskDataZoneResponse]
    total: int


class AskDataTagPage(BaseModel):
    items: list[AskDataTagResponse]
    total: int


class AskDataRulePage(BaseModel):
    items: list[AskDataRuleResponse]
    total: int


class AskDataSensitivePage(BaseModel):
    items: list[AskDataSensitiveFieldResponse]
    total: int


class AskDataSupplementPage(BaseModel):
    items: list[AskDataSupplementResponse]
    total: int


class AskDataImportInput(RequestModel):
    filename: str = Field(default="", max_length=200)
    document: dict


class AskDataImportItem(BaseModel):
    asset: str
    key: str
    action: Literal["create", "update", "skip", "conflict"]
    reason: str = ""


class AskDataImportPreview(BaseModel):
    import_id: UUID
    format: str
    upstream_commit: str
    counts: dict[str, int]
    items: list[AskDataImportItem]
    warnings: list[str]
    model_version_id: UUID | None = None


class AskDataImportApplyInput(RequestModel):
    conflict_policy: Literal["skip", "overwrite"] = "skip"


class AskDataImportResponse(ORMResponse):
    id: UUID
    connection_id: UUID
    model_version_id: UUID | None
    filename: str
    status: str
    conflict_policy: str
    report: dict
    applied_at: AwareDatetime | None
    created_at: AwareDatetime


class AskDataStatisticsTotals(BaseModel):
    runs: int
    completed: int
    clarification: int
    failed: int
    cancelled: int
    success_rate: float
    average_seconds: float
    p50_seconds: float
    p95_seconds: float


class AskDataStatisticPoint(BaseModel):
    label: str
    runs: int
    failed: int


class AskDataStatisticFailure(BaseModel):
    code: str
    count: int


class AskDataStatisticModels(BaseModel):
    calls: int
    input_tokens: float
    output_tokens: float


class AskDataStatisticCertification(BaseModel):
    certified: int
    draft: int
    pending_review: int
    coverage: float


class AskDataStatisticsResponse(BaseModel):
    start: AwareDatetime
    end: AwareDatetime
    days: int
    totals: AskDataStatisticsTotals
    failures: list[AskDataStatisticFailure]
    daily: list[AskDataStatisticPoint]
    models: AskDataStatisticModels
    certification: AskDataStatisticCertification
