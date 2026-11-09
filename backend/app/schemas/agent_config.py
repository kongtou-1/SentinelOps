from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, Field, SecretStr, model_validator

from app.schemas.auth import RequestModel
from app.schemas.llm import EndpointInput
from app.schemas.resources import ORMResponse

AgentModule = Literal["diagnosis", "inspection", "report", "evaluation"]


class ConfigInput(RequestModel):
    enabled: bool
    expected_version: int = Field(ge=1)


class ChannelInput(RequestModel):
    name: str = Field(min_length=1, max_length=100)
    enabled: bool = True
    base_url: str = Field(min_length=1, max_length=2000)
    model: str = Field(min_length=1, max_length=100, pattern=r"^\S+$")
    api_key: SecretStr | None = None
    timeout_seconds: int = Field(default=60, ge=1, le=300)
    expected_version: int = Field(ge=1)

    @model_validator(mode="after")
    def validate_channel(self):
        EndpointInput.validate_endpoint(self)
        return self


class VersionInput(RequestModel):
    expected_version: int = Field(ge=1)


class ChannelOrder(VersionInput):
    channel_ids: list[UUID]


class ChannelResponse(ORMResponse):
    id: UUID
    module: AgentModule
    name: str
    enabled: bool
    position: int
    base_url: str
    model: str
    key_configured: bool = True
    timeout_seconds: int
    last_used_at: AwareDatetime | None
    last_success: bool | None
    last_error: str | None
    last_latency_ms: float | None
    created_at: AwareDatetime
    updated_at: AwareDatetime


class ConfigResponse(ORMResponse):
    module: AgentModule
    enabled: bool
    platform_enabled: bool
    version: int
    channels: list[ChannelResponse]


class ChannelTestResponse(RequestModel):
    success: bool
    error_code: str | None
    latency_ms: float
