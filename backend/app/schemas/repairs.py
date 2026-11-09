from typing import Annotated, Literal
from uuid import UUID

from pydantic import AwareDatetime, Field

from app.schemas.auth import RequestModel
from app.schemas.resources import ORMResponse
from app.schemas.sources import Commit

Note = Annotated[str, Field(min_length=1, max_length=10000, pattern=r"\S")]


class HandoffInput(RequestModel):
    analysis_id: UUID
    application_id: UUID | None = None
    reproduction: str = Field(default="", max_length=10000)
    acceptance_criteria: list[Note] = Field(min_length=1, max_length=20)


class RepairResultInput(RequestModel):
    repair_commit: Commit | None = None
    change_summary: Note
    test_result: Note
    release_id: UUID | None = None
    deployment_id: UUID | None = None


class RepairVerificationInput(RequestModel):
    result_id: UUID
    outcome: Literal["runtime_recovered", "fix_validated", "failed", "inconclusive"]
    observed_at: AwareDatetime
    evidence: Note
    acceptance_results: Note


class HandoffResponse(ORMResponse):
    id: UUID
    incident_id: UUID
    analysis_id: UUID
    application_id: UUID | None
    requested_by: UUID
    packet: dict
    packet_hash: str
    results: list
    verifications: list
    created_at: AwareDatetime
    updated_at: AwareDatetime
