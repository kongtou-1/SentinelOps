from pathlib import PurePosixPath
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from app.schemas.auth import RequestModel
from app.schemas.sources import relative_path


class SourceInput(RequestModel):
    snapshot_id: UUID
    path: str = Field(default=".", max_length=500)

    @field_validator("path")
    @classmethod
    def valid_path(cls, value):
        return relative_path(value)


class SourceSearch(SourceInput):
    query: str = Field(min_length=1, max_length=500)


class SourceFile(SourceInput):
    start_line: int = Field(default=1, ge=1)
    end_line: int = Field(default=200, ge=1)
    runtime_path: str | None = Field(default=None, max_length=500)

    @field_validator("runtime_path")
    @classmethod
    def valid_runtime_path(cls, value):
        if value is not None and (
            not value.startswith("/")
            or "\\" in value
            or ".." in PurePosixPath(value).parts
            or any(ord(c) < 32 for c in value)
        ):
            raise ValueError("Use an absolute runtime path without traversal")
        return value

    @model_validator(mode="after")
    def line_range(self):
        if not self.start_line <= self.end_line < self.start_line + 200:
            raise ValueError("Read at most 200 lines, with end >= start")
        return self


class SourceCompare(RequestModel):
    snapshot_id: UUID
    other_id: UUID
    path: str | None = Field(default=None, max_length=500)

    @field_validator("path")
    @classmethod
    def valid_path(cls, value):
        return relative_path(value) if value is not None else None
