from uuid import UUID

from pydantic import AwareDatetime, BaseModel


class RecycleType(BaseModel):
    kind: str
    label: str
    count: int


class RecycleEntry(BaseModel):
    kind: str
    label: str
    id: UUID
    name: str
    created_at: AwareDatetime
    updated_at: AwareDatetime
    deleted_at: AwareDatetime
    restore_note: str
    blocked_reason: str | None = None


class RestoreInput(BaseModel):
    expected_deleted_at: AwareDatetime


class DeleteInput(BaseModel):
    expected_deleted_at: AwareDatetime


class RestoreResult(BaseModel):
    kind: str
    id: UUID
    restored_children: int = 0
