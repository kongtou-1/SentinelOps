"""Request and response contracts for business-model definition management."""

from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, Field

from app.schemas.auth import RequestModel
from app.schemas.resources import ORMResponse

DefinitionKind = Literal["term", "attribute", "metric"]


class AskDataDefinitionInput(RequestModel):
    object_kind: DefinitionKind
    object_key: str = Field(min_length=1, max_length=300)
    description: str | None = Field(default=None, max_length=2000)
    synonyms: list[str] | None = Field(default=None, max_length=50)
    expression: str | None = Field(default=None, max_length=4000)
    certified: bool | None = None


class AskDataDefinitionResponse(ORMResponse):
    id: UUID
    model_version_id: UUID
    object_kind: str
    object_key: str
    object_id: str | None
    payload: dict
    certified: bool
    review_required: bool
    review_note: str
    edited_by_name: str
    source_fingerprint: str
    created_at: AwareDatetime
    updated_at: AwareDatetime


class AskDataDefinitionPage(BaseModel):
    items: list[AskDataDefinitionResponse]
    total: int
    pending_review: int


class AskDataMetricSummary(BaseModel):
    id: str
    name: str
    description: str
    expression: str
    source: str
    certified: bool


class AskDataMetricPage(BaseModel):
    items: list[AskDataMetricSummary]
    total: int


class AskDataDiffItem(BaseModel):
    object_kind: str
    object_key: str
    before: dict
    after: dict


class AskDataVersionDiff(BaseModel):
    version_id: UUID
    compare_version_id: UUID
    added: list[AskDataDiffItem]
    removed: list[AskDataDiffItem]
    changed: list[AskDataDiffItem]
    summary: dict[str, int]
