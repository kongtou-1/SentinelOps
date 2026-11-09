from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, Field, SecretStr, model_validator

from app.schemas.auth import RequestModel
from app.schemas.resources import ORMResponse
from app.utils.redaction import SENSITIVE


class DatabaseInput(RequestModel):
    application_id: UUID | None = None
    name: str = Field(min_length=1, max_length=100)
    engine: Literal["postgresql", "mysql"]
    host: str = Field(min_length=1, max_length=253, pattern=r"^[A-Za-z0-9.:%_-]+$")
    port: int = Field(ge=1, le=65535)
    database: str = Field(min_length=1, max_length=128, pattern=r"^[\w-]+$")
    username: str = Field(min_length=1, max_length=128)
    password: SecretStr | None = Field(default=None, max_length=4096)
    ca_certificate: SecretStr | None = Field(default=None, max_length=65536)
    tls_mode: Literal["verify-full", "disable"] = "verify-full"
    environment: str = Field(min_length=1, max_length=50)
    purpose: str = Field(default="", max_length=2000)
    description: str = Field(default="", max_length=16000)
    table_notes: dict[str, str] = Field(default_factory=dict, max_length=200)
    allowed_tables: dict[str, list[str]] = Field(default_factory=dict, max_length=200)
    enabled: bool = True
    query_enabled: bool = False

    @model_validator(mode="after")
    def access_scope(self):
        for table, columns in self.allowed_tables.items():
            if len(table) > 260 or len(table.split(".")) != 2 or not all(table.split(".")):
                raise ValueError("Use schema.table names")
            if (
                not columns
                or len(columns) > 200
                or any(not col or len(col) > 128 or SENSITIVE.search(col) for col in columns)
            ):
                raise ValueError("Select explicit non-secret columns")
        if any(len(k) > 260 or len(v) > 8000 for k, v in self.table_notes.items()):
            raise ValueError("Table notes exceed the limit")
        if self.query_enabled and not self.allowed_tables:
            raise ValueError("Authorize tables and columns before enabling queries")
        return self


class DatabaseTestInput(DatabaseInput):
    connection_id: UUID | None = None


class DatabaseResponse(ORMResponse):
    id: UUID
    application_id: UUID | None
    name: str
    engine: str
    host: str
    port: int
    database: str
    username: str
    tls_mode: str
    environment: str
    purpose: str
    description: str
    table_notes: dict[str, str]
    allowed_tables: dict[str, list[str]]
    enabled: bool
    query_enabled: bool
    config_version: int
    schema_hash: str
    schema_refreshed_at: AwareDatetime | None
    last_test_at: AwareDatetime | None
    last_test_success: bool | None
    last_error_code: str | None
    permission_status: str
    password_configured: bool
    ca_configured: bool
    created_at: AwareDatetime
    updated_at: AwareDatetime


class DatabaseTestResponse(RequestModel):
    success: bool
    permission_status: str
    error_code: str | None = None
    tested_at: AwareDatetime


class DatabaseSchemaResponse(RequestModel):
    schema_hash: str
    refreshed_at: AwareDatetime | None
    tables: dict
    truncated: bool = False
    table_notes: dict[str, str]
    description: str


class DatabaseToolInput(RequestModel):
    connection_id: UUID


class DatabaseSchemaInput(DatabaseToolInput):
    tables: list[str] = Field(default_factory=list, max_length=10)


class DatabaseQueryInput(DatabaseToolInput):
    sql: str = Field(min_length=1, max_length=16000)
