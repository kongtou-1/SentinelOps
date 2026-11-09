import json
import re
from decimal import Decimal
from typing import Literal
from urllib.parse import urlsplit
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import AwareDatetime, BaseModel, Field, SecretStr, model_validator

from app.schemas.auth import RequestModel
from app.schemas.resources import ORMResponse
from app.utils.redaction import SENSITIVE, redact


class InspectionSummary(BaseModel):
    runs_total: int = Field(ge=0, description="未删除的全部巡检批次数，包含进行中和失败批次")
    items_total: int = Field(ge=0, description="未删除批次中的去重检查记录数")
    issues_total: int = Field(ge=0, description="规则检查未通过的对象数量")
    services_covered: int = Field(ge=0, description="未删除计划关联的未删除服务数，包含暂停计划")
    plans_enabled: int = Field(ge=0, description="已启用且未删除的计划数，包含独立巡检计划")
    runs_active: int = Field(ge=0, description="排队、读取数据或评估中的未删除巡检批次数")


class InspectionRule(RequestModel):
    code: str = Field(pattern=r"^[a-z][a-z0-9_]{0,49}$")
    title: str = Field(min_length=1, max_length=200)
    kind: Literal[
        "required",
        "equals",
        "field_equals",
        "numeric_range",
        "contains",
        "starts_with",
        "ends_with",
        "count_range",
        "unique",
    ]
    path: str = Field(min_length=1, max_length=200, pattern=r"^[\w.*-]+$")
    compare_path: str = Field(default="", max_length=200, pattern=r"^[\w.*-]*$")
    expected: str | bool | int | float | None = None
    minimum: Decimal | None = Field(default=None, allow_inf_nan=False)
    maximum: Decimal | None = Field(default=None, allow_inf_nan=False)
    absolute: bool = False
    unit: str = Field(default="", max_length=30)
    unit_path: str = Field(default="", max_length=200, pattern=r"^[\w.*-]*$")
    basis: str = Field(min_length=1, max_length=2000)

    @model_validator(mode="after")
    def bounds(self):
        if self.kind in {"contains", "starts_with", "ends_with"} and not isinstance(
            self.expected, str
        ):
            raise ValueError("String conditions require a string expected value")
        if any(SENSITIVE.search(p) for p in (self.path, self.unit_path, self.compare_path)):
            raise ValueError("Secret fields cannot be inspected")
        if self.kind == "field_equals" and not self.compare_path:
            raise ValueError("Specify the comparison field")
        if self.kind in {"numeric_range", "count_range"}:
            if self.minimum is None and self.maximum is None:
                raise ValueError("Specify a numeric bound")
            if self.minimum is not None and self.maximum is not None:
                if self.minimum > self.maximum:
                    raise ValueError("Minimum exceeds maximum")
            if bool(self.unit) != bool(self.unit_path):
                raise ValueError("Specify both unit and unit_path")
        return self


class InspectionCheck(RequestModel):
    check_id: str = Field(pattern=r"^[a-z][a-z0-9_\-]{0,49}$")
    name: str = Field(min_length=1, max_length=200)
    kind: Literal["rule", "agent"]
    enabled: bool = True
    scope: Literal["object", "collection"] = "object"
    severity: Literal["info", "warning", "critical"] = "warning"
    conditions: list[InspectionRule] = Field(default_factory=list, max_length=30)
    operator: Literal["and", "or"] = "and"
    requirements: str = Field(default="", max_length=8000)
    skill_id: UUID | None = None
    auxiliary_skill_ids: list[UUID] = Field(default_factory=list, max_length=3)
    source_ids: list[UUID] = Field(default_factory=list, max_length=20)
    parameters: dict = Field(default_factory=dict)

    @model_validator(mode="after")
    def configuration(self):
        if self.kind == "rule":
            if not self.conditions or self.skill_id or self.source_ids or self.auxiliary_skill_ids:
                raise ValueError("Rule checks require conditions and no Agent configuration")
        elif not self.skill_id or not self.requirements or self.conditions:
            raise ValueError(
                "Agent checks require a Skill and requirements, without rule conditions"
            )
        if len({r.code for r in self.conditions}) != len(self.conditions):
            raise ValueError("Condition codes must be unique")
        if len(set(self.source_ids)) != len(self.source_ids):
            raise ValueError("Query references must be unique")
        if len(set([self.skill_id, *self.auxiliary_skill_ids])) != 1 + len(
            self.auxiliary_skill_ids
        ):
            raise ValueError("Skill references must be unique")
        if (
            redact(self.parameters) != self.parameters
            or len(json.dumps(self.parameters, allow_nan=False).encode()) > 16384
        ):
            raise ValueError("Use bounded non-secret business parameters")
        return self


class InspectionObject(RequestModel):
    id: str = Field(min_length=1, max_length=200)
    facts: dict

    @model_validator(mode="after")
    def bounded(self):
        if (
            redact(self.facts) != self.facts
            or len(json.dumps(self.facts, allow_nan=False).encode()) > 32768
        ):
            raise ValueError("Use bounded non-secret object data")
        return self


class InspectionQueryParameter(RequestModel):
    name: str = Field(min_length=1, max_length=100)
    value: str = Field(max_length=2000)

    @model_validator(mode="after")
    def safe_parameter(self):
        if self.name != self.name.strip() or any(ord(c) < 32 for c in self.name + self.value):
            raise ValueError("Invalid query parameter")
        if self.name.lower() in {
            "updated_after",
            "updated_before",
            "limit",
            "cursor",
            "match_field",
            "match_value",
        }:
            raise ValueError("Inspection paging and lookup parameters are supplied automatically")
        if SENSITIVE.search(self.name):
            raise ValueError("Configure authentication in encrypted request headers")
        return self


class InspectionSource(RequestModel):
    url: str = Field(default="", max_length=2000)
    http_connection_id: UUID | None = None
    agent_api_enabled: bool = False
    request_method: Literal["GET", "POST"] = "GET"
    path_template: str = Field(default="", max_length=500)
    allowed_query_params: list[str] = Field(default_factory=list, max_length=30)
    allowed_body_fields: list[str] = Field(default_factory=list, max_length=30)
    query_params: list[InspectionQueryParameter] = Field(default_factory=list, max_length=20)
    response_format: Literal["records", "json"] = "records"
    items_path: str = Field(default="", max_length=200, pattern=r"^[\w.-]*$")
    object_id_path: str = Field(default="id", max_length=200, pattern=r"^[\w.-]+$")
    connection_id: UUID | None = None
    table: str = Field(default="", max_length=260)
    id_column: str = Field(default="", max_length=128)
    version_column: str = Field(default="", max_length=128, description="留空时按读取内容计算版本")
    time_column: str = Field(
        default="", max_length=128, description="留空时按对象标识扫描，不按时间过滤"
    )
    decision_column: str = Field(default="", max_length=128)
    decision_pass_value: str = Field(default="pass", max_length=100)
    decision_fail_value: str = Field(default="fail", max_length=100)
    naive_time_is_utc: bool = False
    fact_columns: list[str] = Field(default_factory=list, max_length=100)
    pagination: Literal["none", "cursor", "page", "offset"] = "none"
    page_parameter: str = Field(default="page", pattern=r"^[A-Za-z_]\w{0,49}$")
    size_parameter: str = Field(default="page_size", pattern=r"^[A-Za-z_]\w{0,49}$")
    next_cursor_path: str = Field(default="next_cursor", max_length=200, pattern=r"^[\w.-]+$")
    has_more_path: str = Field(default="", max_length=200, pattern=r"^[\w.-]*$")
    query_sql: str = Field(default="", max_length=16000)
    query_parameters: dict[str, str | int | bool | None] = Field(
        default_factory=dict, max_length=30
    )

    @model_validator(mode="after")
    def safe_source(self):
        if self.http_connection_id and self.url:
            raise ValueError("Use either a shared HTTP connection or an inline URL")
        if self.pagination != "none" and self.response_format != "json":
            raise ValueError("Configure JSON mapping for custom pagination")
        if self.page_parameter == self.size_parameter or SENSITIVE.search(
            self.page_parameter + self.size_parameter
        ):
            raise ValueError("Use distinct non-secret pagination parameters")
        if redact(self.query_parameters) != self.query_parameters:
            raise ValueError("SQL parameters cannot contain credentials")
        if len(json.dumps(self.query_parameters, allow_nan=False).encode()) > 16384:
            raise ValueError("SQL parameters exceed 16 KiB")
        template = re.sub(r"\{[A-Za-z_]\w*\}", "item", self.path_template)
        if template and (
            not re.fullmatch(r"[A-Za-z0-9_/-]+", template)
            or template.startswith("/")
            or "//" in template
        ):
            raise ValueError("Use a relative path with named {parameters}")
        for name in [*self.allowed_query_params, *self.allowed_body_fields]:
            if not re.fullmatch(r"[A-Za-z_]\w*", name) or SENSITIVE.search(name):
                raise ValueError("Use non-secret dynamic parameter names")
        if self.request_method == "POST" and not self.agent_api_enabled:
            raise ValueError("POST must be explicitly authorized as an Agent query API")
        if self.decision_pass_value == self.decision_fail_value:
            raise ValueError("Pass and fail values must differ")
        if self.url:
            parsed = urlsplit(self.url)
            if (
                parsed.scheme not in ("http", "https")
                or not parsed.hostname
                or parsed.username
                or parsed.password
                or parsed.query
                or parsed.fragment
                or any(c.isspace() or ord(c) < 32 for c in self.url)
                or "\\" in self.url
            ):
                raise ValueError("Use a fixed HTTP(S) URL without credentials, query or fragment")
            try:
                parsed.port
            except ValueError:
                raise ValueError("Invalid port") from None
        columns = self.fact_columns + [
            self.id_column,
            self.version_column,
            self.time_column,
            self.decision_column,
        ]
        if any(SENSITIVE.search(c) for c in columns if c):
            raise ValueError("Secret columns are not allowed")
        return self


class InspectionRelatedSource(RequestModel):
    data_source_id: UUID | None = None
    name: str = Field(pattern=r"^[a-z][a-z0-9_]{0,29}$")
    source_kind: Literal["api", "database"] = "api"
    source_config: InspectionSource = Field(default_factory=InspectionSource)
    main_field: str = Field(default="", max_length=200, pattern=r"^[\w.-]*$")
    match_field: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z_]\w*(?:\.\w+)*$")
    cardinality: Literal["one", "many"] = "one"
    required: bool = True
    max_records: int = Field(default=20, ge=1, le=50)

    @model_validator(mode="after")
    def safe_relation(self):
        if self.name == "main" or SENSITIVE.search(self.name + self.main_field + self.match_field):
            raise ValueError("Use a non-secret alias other than main")
        if self.data_source_id:
            return self
        validate_source(self.source_kind, self.source_config, None)
        if (
            self.source_kind == "database"
            and self.match_field not in self.source_config.fact_columns
        ):
            raise ValueError("Authorize the lookup column as a fact column")
        return self


class InspectionRelatedSourceInput(InspectionRelatedSource):
    headers: dict[str, SecretStr] | None = Field(default=None, max_length=20)

    @model_validator(mode="after")
    def safe_headers(self):
        if not self.data_source_id:
            validate_source(self.source_kind, self.source_config, self.headers)
        return self


class InspectionRelatedSourceResponse(InspectionRelatedSource):
    headers_configured: bool = False


def validate_source(kind, source, headers):
    if kind == "api":
        if not (source.url or source.http_connection_id) or source.connection_id:
            raise ValueError("API inspections require one fixed URL")
        if source.http_connection_id and headers is not None:
            raise ValueError("Configure credentials on the shared HTTP connection")
    elif (
        not source.connection_id
        or not (source.table or source.query_sql)
        or not source.fact_columns
        or source.url
    ):
        raise ValueError("Database inspections require a connection, table and fact columns")
    if kind == "database":
        if source.http_connection_id:
            raise ValueError("Database queries cannot use an HTTP connection")
        if source.query_params:
            raise ValueError("Query parameters are not used for database inspections")
        if headers:
            raise ValueError("HTTP headers are not used for database inspections")
        if not source.query_sql and not re.fullmatch(r"[A-Za-z_]\w*\.[A-Za-z_]\w*", source.table):
            raise ValueError("Use an ordinary schema.table")
        columns = [
            source.id_column,
            *source.fact_columns,
        ]
        columns.extend(
            c for c in (source.version_column, source.time_column, source.decision_column) if c
        )
        if any(not re.fullmatch(r"[A-Za-z_]\w*", c) for c in columns):
            raise ValueError("Use explicit column names")
    for key, value in (headers or {}).items():
        secret = value.get_secret_value()
        if (
            not key
            or len(key) > 100
            or len(secret) > 4096
            or any(c in key + secret for c in "\r\n")
            or key.lower() in {"host", "content-length", "transfer-encoding", "connection"}
        ):
            raise ValueError("Invalid HTTP header")


class InspectionPlanInput(RequestModel):
    application_id: UUID | None = None
    name: str = Field(min_length=1, max_length=100)
    environment: str = Field(min_length=1, max_length=50)
    enabled: bool = False
    expected_version: int | None = Field(default=None, ge=1)
    interval_seconds: int = Field(default=3600, ge=60, le=604800)
    data_source_id: UUID | None = None
    schedule_kind: Literal["interval", "cron", "once", "manual", "event"] = "interval"
    cron_expression: str = Field(default="", max_length=100)
    schedule_timezone: str = Field(default="Asia/Shanghai", max_length=100)
    scheduled_at: AwareDatetime | None = None
    timeout_seconds: int = Field(default=300, ge=10, le=3600)
    max_retries: int = Field(default=0, ge=0, le=3)
    lookback_seconds: int = Field(default=86400, ge=60, le=2592000)
    max_records: int = Field(default=100, ge=1, le=10000)
    execution_mode: Literal["records", "agent", "checks"] = "records"
    check_items: list[InspectionCheck] = Field(default_factory=list, max_length=30)
    object_mode: Literal["dynamic", "fixed"] = "dynamic"
    fixed_objects: list[InspectionObject] = Field(default_factory=list, max_length=1000)
    agent_source_ids: list[UUID] = Field(default_factory=list, max_length=20)
    agent_parameters: dict = Field(default_factory=dict)
    agent_enabled: bool = False
    max_agent_items: int = Field(default=10, ge=1, le=200)
    skill_id: UUID | None = None
    source_kind: Literal["api", "database"] = "api"
    source_config: InspectionSource = Field(default_factory=InspectionSource)
    related_sources: list[InspectionRelatedSourceInput] = Field(default_factory=list, max_length=4)
    headers: dict[str, SecretStr] | None = Field(default=None, max_length=20)
    rules: list[InspectionRule] = Field(default_factory=list, max_length=30)

    @model_validator(mode="after")
    def complete(self):
        from app.services.inspections.scheduling import next_cron

        try:
            ZoneInfo(self.schedule_timezone)
            if self.schedule_kind == "cron":
                from datetime import UTC, datetime

                next_cron(self.cron_expression, self.schedule_timezone, datetime.now(UTC))
        except (ValueError, ZoneInfoNotFoundError):
            raise ValueError("Invalid Cron expression or timezone") from None
        if self.schedule_kind == "once" and self.scheduled_at is None:
            raise ValueError("Specify the fixed execution time")
        if self.agent_enabled and not self.skill_id:
            raise ValueError("Agent inspection requires a Skill")
        if len(json.dumps(self.agent_parameters, allow_nan=False).encode()) > 16384:
            raise ValueError("Agent parameters exceed 16 KiB")
        if redact(self.agent_parameters) != self.agent_parameters:
            raise ValueError(
                "Store credentials in encrypted data source headers, not Agent parameters"
            )
        if self.check_items:
            self.execution_mode = "checks"
        if self.execution_mode == "checks":
            if not self.check_items or not any(c.enabled for c in self.check_items):
                raise ValueError("Configure at least one enabled check")
            if self.rules or self.agent_enabled or self.agent_source_ids or self.related_sources:
                raise ValueError("Use check_items exclusively for unified checks")
            if len({c.check_id for c in self.check_items}) != len(self.check_items):
                raise ValueError("Check IDs must be unique")
            if self.object_mode == "fixed":
                if not self.fixed_objects or self.data_source_id:
                    raise ValueError(
                        "Fixed objects require explicit objects and no discovery query"
                    )
                if len({o.id for o in self.fixed_objects}) != len(self.fixed_objects):
                    raise ValueError("Object IDs must be unique")
                return self
            if self.fixed_objects:
                raise ValueError("Dynamic discovery does not accept fixed objects")
        elif self.fixed_objects or self.object_mode != "dynamic":
            raise ValueError("Fixed objects require unified checks")
        if self.execution_mode == "agent":
            if not self.agent_enabled or not self.skill_id or not self.data_source_id:
                raise ValueError(
                    "Agent workflow requires a Skill and an authorized API data source"
                )
            if self.rules or self.related_sources:
                raise ValueError("Agent workflows use Skill checks and authorized APIs")
        elif self.agent_source_ids:
            raise ValueError("Additional Agent APIs require Agent workflow mode")
        if not self.rules and not self.agent_enabled and not self.check_items:
            raise ValueError("Configure rules or enable the Agent")
        if len({r.code for r in self.rules}) != len(self.rules):
            raise ValueError("Rule codes must be unique")
        if len({r.name for r in self.related_sources}) != len(self.related_sources):
            raise ValueError("Source aliases must be unique")
        if not self.data_source_id:
            validate_source(self.source_kind, self.source_config, self.headers)
        elif self.headers is not None:
            raise ValueError("Configure credentials on the reusable data source")
        return self


class InspectionPlanResponse(ORMResponse):
    id: UUID
    application_id: UUID | None
    application_name: str | None = None
    configured_by: UUID
    name: str
    environment: str
    enabled: bool
    version: int
    interval_seconds: int
    data_source_id: UUID | None
    schedule_kind: str
    cron_expression: str
    schedule_timezone: str
    scheduled_at: AwareDatetime | None
    timeout_seconds: int
    max_retries: int
    last_run_at: AwareDatetime | None = None
    last_status: str | None = None
    last_result: str | None = None
    last_run_id: UUID | None = None
    group_total: int = 0
    lookback_seconds: int
    max_records: int
    execution_mode: Literal["records", "agent", "checks"] = "records"
    check_items: list[InspectionCheck] = Field(default_factory=list)
    object_mode: Literal["dynamic", "fixed"] = "dynamic"
    fixed_objects: list[InspectionObject] = Field(default_factory=list)
    agent_source_ids: list[UUID] = Field(default_factory=list)
    agent_parameters: dict = Field(default_factory=dict)
    agent_enabled: bool
    max_agent_items: int
    skill_id: UUID | None
    skill_version: int | None
    source_kind: str
    source_config: InspectionSource
    related_sources: list[InspectionRelatedSourceResponse] = []
    rules: list[InspectionRule]
    headers_configured: bool
    next_run_at: AwareDatetime | None
    watermark: AwareDatetime | None
    continuation: dict
    created_at: AwareDatetime
    updated_at: AwareDatetime


class InspectionRunResponse(ORMResponse):
    id: UUID
    plan_id: UUID
    plan_version: int
    trigger: str
    run_kind: Literal["standard", "trial"] = "standard"
    status: str
    snapshot: dict
    window_start: AwareDatetime
    window_end: AwareDatetime
    collected: int
    agent_calls: int
    summary: dict
    error_code: str | None
    error_stage: str | None
    retry_count: int
    retry_at: AwareDatetime | None
    started_at: AwareDatetime | None
    finished_at: AwareDatetime | None
    created_at: AwareDatetime


class InspectionTrialInput(RequestModel):
    plan: InspectionPlanInput
    sample_size: int = Field(default=3, ge=1, le=10)

    @model_validator(mode="after")
    def unified(self):
        if self.plan.execution_mode != "checks":
            raise ValueError("Trial runs require independent check items")
        return self


class InspectionScheduleInput(RequestModel):
    schedule_kind: Literal["interval", "cron", "once", "manual", "event"]
    interval_seconds: int = Field(default=3600, ge=60, le=604800)
    cron_expression: str = Field(default="", max_length=100)
    schedule_timezone: str = Field(default="Asia/Shanghai", max_length=100)
    scheduled_at: AwareDatetime | None = None


class InspectionScheduleResponse(BaseModel):
    times: list[AwareDatetime]


class InspectionItemResponse(ORMResponse):
    id: UUID
    run_id: UUID
    plan_id: UUID
    record_id: str
    record_version: str
    observed_at: AwareDatetime
    evidence: dict
    checks: list[dict]
    verdict: str
    rule_verdict: str | None
    check_status: str | None = None
    final_status: str = "unknown"
    explanation: str = ""
    agent_status: str
    agent_result: dict
    created_at: AwareDatetime


class InspectionCheckResultResponse(ORMResponse):
    id: UUID
    run_id: UUID
    plan_id: UUID
    item_id: UUID | None
    object_key: str
    check_id: str
    kind: Literal["rule", "agent"]
    scope: Literal["object", "collection"]
    execution_status: Literal["pending", "running", "completed", "error"]
    status: Literal["pass", "fail", "unknown"]
    severity: Literal["info", "warning", "critical"] | None
    summary: str
    result: dict
    error_code: str | None
    started_at: AwareDatetime | None
    finished_at: AwareDatetime | None
    created_at: AwareDatetime


class InspectionTraceResponse(ORMResponse):
    id: UUID
    run_id: UUID
    check_result_id: UUID
    object_key: str | None = None
    check_id: str | None = None
    check_name: str | None = None
    tool: str
    success: bool
    data: dict
    created_at: AwareDatetime


class InspectionProgressEvent(BaseModel):
    id: str
    occurred_at: AwareDatetime
    kind: Literal[
        "queued", "started", "object", "check_started", "check_finished", "tool", "finished"
    ]
    status: str
    object_key: str | None = None
    check_name: str | None = None
    tool: str | None = None
    summary: str = ""
    error_code: str | None = None
    data: dict = Field(default_factory=dict)


class InspectionProgressResponse(BaseModel):
    status: str
    current: InspectionProgressEvent | None = None
    events: list[InspectionProgressEvent]
    next_cursor: str | None = None


class InspectionQueryPreview(RequestModel):
    limit: int = Field(default=3, ge=1, le=10)
    parameters: dict[str, str | int | bool | None] = Field(default_factory=dict, max_length=30)


class InspectionQueryPreviewResponse(RequestModel):
    records: list[dict]
    next_cursor: dict


class InspectionIssue(RequestModel):
    code: str = Field(pattern=r"^[a-z][a-z0-9_\-]{0,79}$")
    summary: str = Field(min_length=1, max_length=1000)
    evidence_ids: list[UUID] = Field(default_factory=list, max_length=30)
    object_ids: list[str] = Field(default_factory=list, max_length=200)


class InspectionSubmittedResult(RequestModel):
    status: Literal["pass", "fail", "unknown"]
    summary: str = Field(min_length=1, max_length=4000)
    details: str = Field(default="", max_length=8000)
    findings: list[InspectionIssue] = Field(default_factory=list, max_length=100)
    evidence_ids: list[UUID] = Field(min_length=1, max_length=50)
    metrics: dict[str, float | int | str] = Field(default_factory=dict, max_length=30)

    @model_validator(mode="after")
    def bounded(self):
        if len(json.dumps(self.model_dump(mode="json"), allow_nan=False).encode()) > 32768:
            raise ValueError("Result exceeds 32 KiB")
        return self


class InspectionDataSourceInput(RequestModel):
    name: str = Field(min_length=1, max_length=100)
    application_id: UUID | None = None
    environment: str = Field(min_length=1, max_length=50)
    source_kind: Literal["api", "database"]
    source_config: InspectionSource
    headers: dict[str, SecretStr] | None = Field(default=None, max_length=20)
    enabled: bool = True
    expected_version: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def complete(self):
        validate_source(self.source_kind, self.source_config, self.headers)
        return self


class InspectionDataSourceResponse(ORMResponse):
    id: UUID
    name: str
    application_id: UUID | None
    environment: str
    source_kind: str
    source_config: InspectionSource
    enabled: bool
    version: int
    headers_configured: bool
    last_test_at: AwareDatetime | None
    last_test_success: bool | None
    last_error_code: str | None
    created_at: AwareDatetime
    updated_at: AwareDatetime


class InspectionConnectionTestResponse(RequestModel):
    success: bool
    tested_at: AwareDatetime
    error_code: str | None = None
    records_read: int = 0


class InspectionHTTPConnectionInput(RequestModel):
    name: str = Field(min_length=1, max_length=100)
    application_id: UUID | None = None
    environment: str = Field(min_length=1, max_length=50)
    base_url: str = Field(min_length=1, max_length=2000)
    headers: dict[str, SecretStr] | None = Field(default=None, max_length=20)
    enabled: bool = True
    expected_version: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def safe(self):
        validate_source("api", InspectionSource(url=self.base_url), self.headers)
        return self


class InspectionHTTPConnectionResponse(ORMResponse):
    id: UUID
    name: str
    application_id: UUID | None
    environment: str
    base_url: str
    enabled: bool
    version: int
    headers_configured: bool
    created_at: AwareDatetime
    updated_at: AwareDatetime


class InspectionTriggerInput(RequestModel):
    kind: Literal["event", "scan"] = "event"


class InspectionReviewInput(RequestModel):
    verdict: Literal["false_reject", "false_accept", "original_correct", "insufficient_evidence"]
    comment: str = Field(min_length=1, max_length=4000)


class InspectionStateInput(RequestModel):
    enabled: bool
    expected_version: int = Field(ge=1)


class InspectionReviewResponse(ORMResponse):
    id: UUID
    item_id: UUID
    reviewer_id: UUID
    verdict: str
    comment: str
    created_at: AwareDatetime


class InspectionRecord(RequestModel):
    id: str = Field(min_length=1, max_length=200)
    version: str = Field(min_length=1, max_length=200)
    updated_at: AwareDatetime
    facts: dict
    original_verdict: Literal["pass", "fail", "unknown"] = "unknown"
    evidence: list[dict] = Field(default_factory=list, max_length=30)

    @model_validator(mode="after")
    def bounded(self):
        if len(json.dumps(self.model_dump(mode="json"), allow_nan=False).encode()) > 32768:
            raise ValueError("Record exceeds 32 KiB")
        return self


class InspectionAgentResult(RequestModel):
    verdict: Literal["supported", "contradicted", "needs_review", "unassessable"]
    summary: str = Field(min_length=1, max_length=4000)
    evidence_ids: list[str] = Field(min_length=1, max_length=30)
    data_gaps: list[str] = Field(default_factory=list, max_length=20)


class InspectionAPIRequest(RequestModel):
    """Call only a plan-bound query endpoint. Never accept a URL or headers."""

    source_id: UUID
    path_params: dict[str, str] = Field(default_factory=dict, max_length=10)
    query: dict[str, str | int | bool] = Field(default_factory=dict, max_length=30)
    body: dict = Field(default_factory=dict, max_length=30)


class InspectionPythonRequest(RequestModel):
    code: str = Field(default="", max_length=32768)
    script: str = Field(default="", max_length=150)
    input_ids: list[str] = Field(default_factory=list, max_length=20)
    inputs: dict = Field(default_factory=dict)

    @model_validator(mode="after")
    def one_program(self):
        if bool(self.code) == bool(self.script):
            raise ValueError("Provide code or a Skill script name")
        if len(json.dumps(self.inputs, allow_nan=False).encode()) > 32768:
            raise ValueError("Python inputs exceed 32 KiB")
        return self


class InspectionFinding(RequestModel):
    record_id: str = Field(min_length=1, max_length=200)
    status: Literal["pass", "fail", "unknown"]
    summary: str = Field(min_length=1, max_length=4000)
    evidence_ids: list[str] = Field(min_length=1, max_length=30)


class InspectionWorkflowResult(RequestModel):
    summary: str = Field(min_length=1, max_length=4000)
    complete: bool
    no_matches: bool = False
    evidence_ids: list[str] = Field(min_length=1, max_length=30)
    findings: list[InspectionFinding] = Field(default_factory=list, max_length=200)
