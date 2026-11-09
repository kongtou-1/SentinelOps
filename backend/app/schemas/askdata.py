from uuid import UUID

from pydantic import AwareDatetime, BaseModel, Field

from app.schemas.auth import RequestModel
from app.schemas.resources import ORMResponse
from app.schemas.service_databases import DatabaseResponse


class AskDataConnectionResponse(DatabaseResponse):
    askdata_enabled: bool
    askdata_version: int


class AskDataConfigurationInput(RequestModel):
    enabled: bool = True
    expected_version: int = Field(default=0, ge=0)


class AskDataConfigurationResponse(ORMResponse):
    connection_id: UUID
    enabled: bool
    version: int
    active_model_version_id: UUID | None = None


class AskDataContextResponse(RequestModel):
    connection_id: UUID
    application_id: UUID | None
    environment: str
    config_version: int
    schema_hash: str
    schema_refreshed: bool
    query_enabled: bool
    permission_status: str
    enabled: bool
    version: int
    active_model_version_id: UUID | None = None


class AskDataVersionInput(RequestModel):
    source_snapshot_id: UUID | None = None


class AskDataBuildResponse(ORMResponse):
    id: UUID
    status: str
    step: str
    error_code: str | None
    detail: dict
    cancel_requested: bool
    started_at: AwareDatetime | None
    finished_at: AwareDatetime | None
    created_at: AwareDatetime
    updated_at: AwareDatetime


class AskDataVersionResponse(ORMResponse):
    id: UUID
    connection_id: UUID
    source_snapshot_id: UUID | None
    created_by: UUID
    created_by_name: str
    config_version: int
    schema_hash: str
    status: str
    context: dict
    published_at: AwareDatetime | None
    active: bool
    build: AskDataBuildResponse | None
    created_at: AwareDatetime
    updated_at: AwareDatetime


class AskDataSchemaSummary(BaseModel):
    name: str
    description: str
    table_count: int
    term_count: int


class AskDataCatalogSummary(BaseModel):
    version_id: UUID
    tables: int
    columns: int
    terms: int
    attributes: int
    relationships: int
    schemas: list[AskDataSchemaSummary]


class AskDataTableSummary(BaseModel):
    id: str
    schema_name: str
    name: str
    description: str
    table_type: str
    is_junction_table: bool
    column_count: int
    term_count: int
    relationship_count: int


class AskDataTablePage(BaseModel):
    items: list[AskDataTableSummary]
    total: int


class AskDataColumn(BaseModel):
    id: str
    name: str
    data_type: str
    nullable: bool
    description: str
    ordinal_position: int | None
    is_unique: bool | None
    sample_values: list
    primary_key: bool
    term_name: str | None
    attribute_name: str | None


class AskDataTermRef(BaseModel):
    id: str
    name: str
    description: str


class AskDataForeignKey(BaseModel):
    columns: list[str]
    referenced_schema: str
    referenced_table: str
    referenced_columns: list[str]
    kind: str


class AskDataTableDetail(BaseModel):
    id: str
    schema_name: str
    name: str
    description: str
    table_type: str
    is_junction_table: bool
    columns: list[AskDataColumn]
    terms: list[AskDataTermRef]
    foreign_keys: list[AskDataForeignKey]


class AskDataTermSummary(BaseModel):
    id: str
    name: str
    description: str
    synonyms: list[str]
    attribute_count: int
    table_names: list[str]


class AskDataTermPage(BaseModel):
    items: list[AskDataTermSummary]
    total: int


class AskDataTermAttribute(BaseModel):
    id: str
    name: str
    description: str
    datatype: str
    source_column: str
    table_id: str
    table_name: str
    certified: bool


class AskDataTermDetail(BaseModel):
    id: str
    name: str
    description: str
    synonyms: list[str]
    attributes: list[AskDataTermAttribute]
    tables: list[AskDataTermRef]


class AskDataRelationship(BaseModel):
    source_schema: str
    source_table: str
    source_column: str
    target_schema: str
    target_table: str
    target_column: str
    kind: str


class AskDataRelationshipPage(BaseModel):
    items: list[AskDataRelationship]
    total: int
    declared: int
    semantic: int
