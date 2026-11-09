from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, Field, model_validator

from app.schemas.auth import RequestModel
from app.schemas.resources import ORMResponse


class BackupArtifact(RequestModel):
    name: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,199}$")
    sha256: str = Field(pattern=r"^[a-fA-F0-9]{64}$")
    bytes: int = Field(ge=0)


class BackupInput(RequestModel):
    kind: Literal["backup", "restore"]
    backup_id: UUID | None = None
    idempotency_key: UUID
    artifacts: list[BackupArtifact] = Field(min_length=1, max_length=50)
    completed_at: AwareDatetime
    duration_seconds: float | None = Field(default=None, ge=0, le=2592000, allow_inf_nan=False)
    verification: Literal["verified", "failed", "not_checked"]
    evidence: str = Field(min_length=1, max_length=8000)

    @model_validator(mode="after")
    def restore_source(self):
        if (self.kind == "restore") != (self.backup_id is not None):
            raise ValueError("Restore records require a source backup; backup records have none")
        return self


class BackupResponse(ORMResponse):
    id: UUID
    kind: str
    backup_id: UUID | None
    requested_by: UUID
    artifacts: list[BackupArtifact]
    completed_at: AwareDatetime
    duration_seconds: float | None
    verification: str
    evidence: str
    created_at: AwareDatetime
