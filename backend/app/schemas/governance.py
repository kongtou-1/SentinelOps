from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, Field

from app.schemas.auth import RequestModel
from app.schemas.resources import ORMResponse

RetentionKind = Literal["conversation", "source_snapshot", "prediction", "temporary"]
Purpose = Literal[
    "business", "probe", "diagnosis", "inspection", "report", "evaluation", "connection_test"
]


class CleanupPreview(RequestModel):
    kinds: list[RetentionKind] = Field(
        default=["conversation", "source_snapshot", "prediction", "temporary"],
        min_length=1,
        max_length=4,
    )
    limit: int = Field(default=50, ge=1, le=100)


class CleanupConfirm(RequestModel):
    expected_version: int = Field(ge=1)
    confirm: Literal[True]


class CleanupResponse(ORMResponse):
    id: UUID
    created_by: UUID
    confirmed_by: UUID | None
    status: str
    candidates: list
    result: list
    version: int
    confirmed_at: AwareDatetime | None
    finished_at: AwareDatetime | None
    error_code: str | None
    created_at: AwareDatetime


class ArchiveResponse(ORMResponse):
    id: UUID
    plan_id: UUID
    resource_kind: str
    resource_id: UUID
    owner_id: UUID | None
    status: str
    sha256: str
    archive_bytes: int
    released_file_bytes: int
    database_reusable_bytes: int
    metadata_snapshot: dict
    created_at: AwareDatetime
