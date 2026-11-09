from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, Field, SecretStr, field_validator, model_validator

from app.schemas.auth import RequestModel, notification_address
from app.schemas.resources import ORMResponse


class ResponsePolicyInput(RequestModel):
    primary_id: UUID | None = None
    backup_id: UUID | None = None
    notify_user_ids: list[UUID] = Field(default_factory=list, max_length=20)
    critical_seconds: int = Field(default=300, ge=60, le=86400)
    warning_seconds: int = Field(default=900, ge=60, le=86400)


class NotificationPolicyInput(RequestModel):
    expected_version: int = Field(ge=0)
    managed: bool = False
    email_enabled: bool = False
    confirm_legacy_switch: bool = False

    @model_validator(mode="after")
    def compatible(self):
        if self.email_enabled and not self.managed:
            raise ValueError("Enable managed notifications before email")
        return self


class NotificationPolicyResponse(RequestModel):
    version: int
    managed: bool
    email_enabled: bool
    configured_by: UUID | None


class MailSettingsInput(RequestModel):
    expected_version: int = Field(ge=0)
    host: str = Field(min_length=1, max_length=253, pattern=r"^[a-zA-Z0-9_.:\-]+$")
    port: int = Field(ge=1, le=65535)
    security: Literal["ssl", "starttls", "none"] = "ssl"
    sender: str = Field(min_length=1, max_length=254)
    username: str = Field(default="", max_length=254, pattern=r"^[^\r\n]*$")
    password: SecretStr | None = Field(default=None, min_length=1, max_length=4096)

    _sender = field_validator("sender")(notification_address)

    @model_validator(mode="after")
    def protected_login(self):
        if self.username and self.security == "none":
            raise ValueError("Use SSL/TLS or STARTTLS for an authenticated mailbox")
        return self


class MailSettingsResponse(RequestModel):
    version: int
    source: Literal["platform", "deployment"]
    host: str
    port: int
    security: Literal["ssl", "starttls", "none"]
    sender: str
    username: str
    password_configured: bool
    local_test_service: bool


class MailTestInput(RequestModel):
    expected_version: int = Field(ge=0)
    recipient: str = Field(min_length=1, max_length=254)

    _recipient = field_validator("recipient")(notification_address)


class MailTestResponse(RequestModel):
    success: bool


class MaintenanceInput(RequestModel):
    scope_kind: Literal["service", "server", "rule"]
    scope_id: UUID
    kind: Literal["maintenance", "silence"] = "maintenance"
    reason: str = Field(min_length=1, max_length=2000, pattern=r"\S")
    starts_at: AwareDatetime
    ends_at: AwareDatetime
    expected_version: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def times(self):
        if self.ends_at <= self.starts_at:
            raise ValueError("End must follow start")
        return self


class MaintenanceResponse(ORMResponse):
    id: UUID
    scope_kind: str
    scope_id: UUID
    kind: str
    reason: str
    starts_at: AwareDatetime
    ends_at: AwareDatetime
    version: int
    created_at: AwareDatetime
    updated_at: AwareDatetime


class InhibitionInput(RequestModel):
    parent_rule_id: UUID
    child_rule_id: UUID


class InhibitionResponse(ORMResponse):
    id: UUID
    parent_rule_id: UUID
    child_rule_id: UUID
    created_at: AwareDatetime
    updated_at: AwareDatetime


class NotificationResponse(ORMResponse):
    id: UUID
    user_id: UUID
    incident_id: UUID | None
    kind: str
    resource_kind: str
    resource_id: UUID
    title: str
    detail: dict
    read_at: AwareDatetime | None
    mail_status: str
    error_code: str | None
    created_at: AwareDatetime
    updated_at: AwareDatetime


class OperationsAuditResponse(ORMResponse):
    id: UUID
    user_id: UUID | None
    actor_username: str | None = None
    action: str
    resource_kind: str
    resource_id: UUID | None
    resource_name: str | None = None
    detail: dict
    created_at: AwareDatetime
