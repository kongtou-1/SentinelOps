from typing import Annotated, Literal
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import AwareDatetime, Field, SecretStr, model_validator

from app.schemas.auth import RequestModel
from app.schemas.resources import Name, ORMResponse

LLMMetric = Literal[
    "llm_up",
    "llm_latency_seconds",
    "llm_success_rate",
    "llm_latency_avg_seconds",
    "llm_latency_p95_seconds",
    "llm_error_rate",
    "llm_http_status",
    "llm_authentication_failed",
    "llm_insufficient_balance",
    "llm_rate_limited",
    "llm_service_error",
    "llm_probe_stale",
]


class EndpointInput(RequestModel):
    name: Name
    provider: Literal["deepseek"] = "deepseek"
    base_url: str = Field(min_length=1, max_length=2000)
    model: str = Field(min_length=1, max_length=100, pattern=r"^\S+$")
    probe_enabled: bool = True
    probe_interval_seconds: int = Field(default=60, ge=60, le=86400)
    api_key: SecretStr | None = None
    agent_channel_id: UUID | None = None

    @model_validator(mode="after")
    def validate_endpoint(self):
        try:
            url = urlsplit(self.base_url)
            if (
                url.scheme not in ("http", "https")
                or not url.hostname
                or url.username is not None
                or url.password is not None
                or "?" in self.base_url
                or "#" in self.base_url
                or any(c.isspace() or ord(c) < 32 for c in self.base_url)
                or (url.port is not None and not 1 <= url.port <= 65535)
            ):
                raise ValueError
        except ValueError:
            raise ValueError(
                "Base URL must be HTTP(S), without credentials, query or fragment"
            ) from None
        self.base_url = self.base_url.rstrip("/")
        if self.api_key is not None:
            key = self.api_key.get_secret_value()
            if not 1 <= len(key) <= 4096 or any(ord(c) < 33 or ord(c) > 126 for c in key):
                raise ValueError("API key must contain 1 to 4096 visible ASCII characters")
        return self

    @model_validator(mode="after")
    def validate_key_source(self):
        if self.api_key is not None and self.agent_channel_id is not None:
            raise ValueError("Choose one API key source")
        return self


class EndpointCreate(EndpointInput):
    @model_validator(mode="after")
    def require_key_source(self):
        if self.api_key is None and self.agent_channel_id is None:
            raise ValueError("Provide an API key or select an Agent channel")
        return self


class EndpointResponse(ORMResponse):
    id: UUID
    name: str
    provider: str
    base_url: str
    model: str
    agent_channel_id: UUID | None
    probe_enabled: bool
    probe_interval_seconds: int
    last_probe_at: AwareDatetime | None
    last_probe_success: bool | None
    last_error_type: str | None
    last_http_status: int | None
    last_latency_ms: float | None
    status: str
    created_at: AwareDatetime
    updated_at: AwareDatetime


class ProbeResult(RequestModel):
    endpoint_id: UUID
    checked_at: AwareDatetime
    success: bool
    error_type: str | None
    http_status: int | None
    latency_ms: float
    input_tokens: Annotated[int | None, Field(ge=0)] = None
    output_tokens: Annotated[int | None, Field(ge=0)] = None


class LLMStatusBucket(RequestModel):
    start: AwareDatetime
    end: AwareDatetime
    request_count: int
    success_count: int


class LLMStatusResponse(RequestModel):
    endpoint_id: UUID
    source: Literal["active_probe"] = "active_probe"
    status: str
    start: AwareDatetime
    end: AwareDatetime
    updated_at: AwareDatetime
    last_request_at: AwareDatetime | None
    request_count: int
    success_count: int
    success_rate: float | None
    buckets: list[LLMStatusBucket]


class LLMBindingInput(RequestModel):
    purpose: str = Field(default="", max_length=2000)


class LLMBindingResponse(ORMResponse):
    id: UUID
    application_id: UUID
    application_name: str
    llm_endpoint_id: UUID
    endpoint_name: str
    model: str
    status: str
    purpose: str
    created_at: AwareDatetime
    updated_at: AwareDatetime
