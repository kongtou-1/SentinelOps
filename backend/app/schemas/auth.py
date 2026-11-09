from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator

Username = Annotated[str, Field(min_length=3, max_length=64, pattern=r"^[a-z0-9_.-]+$")]
RoleCode = Literal["admin", "operator", "viewer"]


def notification_address(value):
    if not value:
        return ""
    from email_validator import EmailNotValidError, validate_email

    try:
        email = validate_email(value, check_deliverability=False, test_environment=True).ascii_email
    except EmailNotValidError:
        raise ValueError("Invalid notification email") from None
    if not email:
        raise ValueError("Use an ASCII notification email")
    return email


class RequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)


class LoginRequest(RequestModel):
    username: Username
    password: SecretStr

    @field_validator("password")
    @classmethod
    def login_password_length(cls, value: SecretStr) -> SecretStr:
        if not 1 <= len(value.get_secret_value()) <= 128:
            raise ValueError("Password must contain 1 to 128 characters")
        return value


class RefreshRequest(RequestModel):
    refresh_token: SecretStr


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str
    expires_in: int


class UserCreate(LoginRequest):
    role: RoleCode = "viewer"
    notification_email: str = Field(default="", max_length=254)

    _email = field_validator("notification_email")(notification_address)

    @field_validator("password")
    @classmethod
    def password_strength(cls, value: SecretStr) -> SecretStr:
        if len(value.get_secret_value()) < 12:
            raise ValueError("Password must contain at least 12 characters")
        return value


class UserUpdate(RequestModel):
    role: RoleCode | None = None
    is_active: bool | None = None
    password: SecretStr | None = None
    notification_email: str | None = Field(default=None, max_length=254)

    _email = field_validator("notification_email")(notification_address)

    @model_validator(mode="after")
    def validate_update(self):
        if not self.model_fields_set or any(
            getattr(self, field) is None for field in self.model_fields_set
        ):
            raise ValueError("Provide at least one non-null field")
        if self.password is not None and not 12 <= len(self.password.get_secret_value()) <= 128:
            raise ValueError("Password must contain 12 to 128 characters")
        return self


class UserResponse(BaseModel):
    id: UUID
    username: str
    role: RoleCode
    is_active: bool
    last_login_at: datetime | None
    created_at: datetime
    updated_at: datetime
    notification_email: str = ""


class MeResponse(UserResponse):
    permissions: list[str]
    permissions_version: str = "1"
    dashboard_access: dict[str, bool] = Field(default_factory=dict)
    default_permissions: list[str] = Field(default_factory=list)


class RoleResponse(BaseModel):
    code: RoleCode
    name: str
    permissions: list[str]
    permissions_version: int = 1
