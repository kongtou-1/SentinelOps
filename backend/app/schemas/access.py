from datetime import datetime
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator

ResourceKind = Literal[
    "global",
    "dashboard",
    "server",
    "container",
    "application",
    "llm_endpoint",
    "service_monitor",
    "service_database",
]


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RequestItem(Input):
    bundle_ids: list[str] = Field(min_length=1, max_length=30)
    resource_kind: ResourceKind
    resource_id: str = Field(min_length=1, max_length=100)
    include_descendants: bool = True


class AccessRequestInput(Input):
    idempotency_key: UUID = Field(default_factory=uuid4)
    items: list[RequestItem] = Field(min_length=1, max_length=30)
    reason: str = Field(min_length=1, max_length=2000)
    duration_days: Literal[1, 7, 30] | None = 7

    @field_validator("reason")
    @classmethod
    def reason_not_blank(cls, value):
        if not value.strip():
            raise ValueError("请填写申请理由")
        return value.strip()


class AccessDecision(Input):
    duration_days: Literal[1, 7, 30] | None = None
    comment: str = Field(default="", max_length=2000)


class RolePermissionsInput(Input):
    expected_version: int = Field(ge=1)
    permissions: list[str] = Field(max_length=200)


class PolicyInput(Input):
    restricted: bool
    expected_version: int = Field(ge=0)


class PolicyResponse(BaseModel):
    resource_kind: ResourceKind
    resource_id: str
    user_id: UUID | None
    restricted: bool
    version: int
    allowed: bool | None


class AccessCheckItem(Input):
    permission: str = Field(max_length=80)
    resource_kind: ResourceKind | None = None
    resource_id: str | None = Field(default=None, max_length=100)
    include_descendants: bool = False


class AccessChecks(Input):
    items: list[AccessCheckItem] = Field(min_length=1, max_length=100)


class AccessRequestResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    user_id: UUID
    items: list[dict]
    reason: str
    duration_days: int | None
    status: str
    decided_by: UUID | None
    decided_at: datetime | None
    decision_comment: str
    created_at: datetime
    updated_at: datetime


class AccessGrantResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    request_id: UUID
    user_id: UUID
    permission: str
    resource_kind: str
    resource_id: str
    include_descendants: bool
    expires_at: datetime | None
    revoked_at: datetime | None
    created_at: datetime
