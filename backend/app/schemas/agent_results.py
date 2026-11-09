"""Bounded navigation of retained business results; raw history stays in its owning tables."""

from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, Field

from app.schemas.auth import RequestModel
from app.schemas.diagnosis import Content, Tags, Title

ResultKind = Literal["diagnosis", "conversation", "inspection_item", "inspection_check"]
Business = Literal["diagnosis", "inspection"]
Section = Literal["result", "evidence", "configuration", "messages"]


class HistorySearch(RequestModel):
    query: str = Field(default="", max_length=200)
    offset: int = Field(default=0, ge=0, le=10000)
    limit: int = Field(default=3, ge=1, le=10)


class HistoryRead(RequestModel):
    kind: ResultKind
    result_id: UUID
    section: Section = "result"
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=4000, ge=1, le=8000)


class ResultSummary(RequestModel):
    kind: ResultKind
    id: UUID
    scope_id: UUID
    run_id: UUID
    title: str
    summary: str
    outcome: str
    created_at: AwareDatetime
    historical: Literal[True] = True


class ResultPage(RequestModel):
    items: list[ResultSummary]
    total: int
    next_offset: int | None


class ResultDetail(RequestModel):
    record: ResultSummary
    section: Section
    content: str
    offset: int
    next_offset: int | None
    total_chars: int


class ResultKnowledgeDraft(RequestModel):
    title: Title
    content: Content
    tags: Tags = []
