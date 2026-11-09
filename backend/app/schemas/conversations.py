from uuid import UUID

from pydantic import AwareDatetime, Field

from app.schemas.auth import RequestModel
from app.schemas.diagnosis import Diagnosis
from app.schemas.resources import ORMResponse


class ConversationInput(RequestModel):
    title: str = Field(default="事故诊断", min_length=1, max_length=200)


class MessageInput(RequestModel):
    client_message_id: UUID
    content: str = Field(min_length=1, max_length=16000)


class RunResponse(ORMResponse):
    id: UUID
    conversation_id: UUID
    incident_id: UUID
    requested_by: UUID
    status: str
    config: dict
    source_context: dict
    database_context: dict
    session_id: str | None
    started_at: AwareDatetime | None
    finished_at: AwareDatetime | None
    error_code: str | None
    model_requests: int
    tool_count: int
    evidence: list
    knowledge: list
    tool_calls: list
    candidate: Diagnosis | dict
    adopted_analysis_id: UUID | None
    created_at: AwareDatetime
    updated_at: AwareDatetime


class ConversationResponse(ORMResponse):
    id: UUID
    incident_id: UUID
    owner_id: UUID
    title: str
    last_seq: int
    created_at: AwareDatetime
    updated_at: AwareDatetime
    owner_name: str
    current_run: RunResponse | None


class MessageResponse(ORMResponse):
    id: UUID
    conversation_id: UUID
    run_id: UUID | None
    role: str
    content: str
    status: str
    client_message_id: UUID | None
    sdk_message_id: str | None
    created_at: AwareDatetime
    updated_at: AwareDatetime


class EventResponse(ORMResponse):
    id: UUID
    conversation_id: UUID
    run_id: UUID | None
    seq: int
    kind: str
    data: dict
    created_at: AwareDatetime
    updated_at: AwareDatetime
