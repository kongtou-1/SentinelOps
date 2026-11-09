import re
from pathlib import PurePosixPath
from typing import Annotated, Literal
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import AwareDatetime, Field, SecretStr, field_validator, model_validator

from app.schemas.auth import RequestModel
from app.schemas.resources import BusinessServiceCreate, BusinessServiceUpdate, ORMResponse

Commit = Annotated[str, Field(pattern=r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")]
Revision = Annotated[str, Field(pattern=r"^(?:0|[1-9][0-9]{0,18})$")]
ImageID = Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]


def relative_path(value):
    if (
        not value
        or "\\" in value
        or ":" in value
        or "\x00" in value
        or value.startswith("/")
        or ".." in PurePosixPath(value).parts
        or any(ord(c) < 32 for c in value)
    ):
        raise ValueError("Use a relative POSIX path within the configured root")
    return str(PurePosixPath(value))


class PathMapping(RequestModel):
    runtime_prefix: str = Field(min_length=1, max_length=500)
    repository_prefix: str = Field(default=".", max_length=500)

    @field_validator("runtime_prefix")
    @classmethod
    def runtime_path(cls, value):
        if not value.startswith("/") or ".." in PurePosixPath(value).parts or "\\" in value:
            raise ValueError("Runtime prefix must be an absolute POSIX path")
        return str(PurePosixPath(value))

    @field_validator("repository_prefix")
    @classmethod
    def repository_path(cls, value):
        return relative_path(value)


class SourceConnectionInput(RequestModel):
    entrypoint: str = Field(default="", max_length=500)
    source_subdir: str = Field(default=".", max_length=500)
    path_mappings: list[PathMapping] = Field(default_factory=list, max_length=20)
    git_url: str | None = Field(default=None, max_length=2000)
    git_ref: str = Field(default="HEAD", min_length=1, max_length=300)
    git_username: str = Field(default="", max_length=200)
    git_token: SecretStr | None = Field(default=None, max_length=10000)
    clear_git_token: bool = False
    svn_url: str | None = Field(default=None, max_length=2000)
    svn_revision: str = Field(default="HEAD", pattern=r"^(?:HEAD|0|[1-9][0-9]{0,18})$")
    svn_username: str = Field(default="", max_length=200)
    svn_password: SecretStr | None = Field(default=None, max_length=10000)
    clear_svn_password: bool = False
    local_path: str | None = Field(default=None, max_length=500)
    enabled: bool = True

    @field_validator("source_subdir", "local_path")
    @classmethod
    def paths(cls, value):
        return relative_path(value) if value is not None else None

    @field_validator("git_url", "svn_url")
    @classmethod
    def url(cls, value):
        if value is None:
            return value
        url = urlsplit(value)
        if (
            url.scheme != "https"
            or not url.hostname
            or url.username
            or url.password
            or url.query
            or url.fragment
            or any(ord(c) < 33 for c in value)
        ):
            raise ValueError("Repository URL must be HTTPS without credentials, query or fragment")
        return value.rstrip("/")

    @field_validator("git_ref")
    @classmethod
    def ref(cls, value):
        if (
            not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_./-]*", value)
            or ".." in value
            or "//" in value
            or value.endswith(("/", ".", ".lock"))
        ):
            raise ValueError("Provide a branch, tag or full commit ID")
        return value

    @field_validator("git_username", "svn_username")
    @classmethod
    def username(cls, value):
        if any(ord(c) < 32 for c in value):
            raise ValueError("Repository username cannot contain control characters")
        return value

    @field_validator("git_token", "svn_password")
    @classmethod
    def token(cls, value):
        if value is not None and (
            not value.get_secret_value() or any(ord(c) < 32 for c in value.get_secret_value())
        ):
            raise ValueError("Repository credential must be a non-empty single-line secret")
        return value

    @model_validator(mode="after")
    def mappings(self):
        if self.git_url and self.svn_url:
            raise ValueError("Choose one Git or SVN repository, with an optional local fallback")
        prefixes = [m.runtime_prefix for m in self.path_mappings]
        if len(prefixes) != len(set(prefixes)):
            raise ValueError("Runtime prefixes must be unique")
        return self


class ApplicationInput(SourceConnectionInput):
    code: str = Field(min_length=1, max_length=100, pattern=r"^[a-z][a-z0-9_.-]*$")
    name: str = Field(min_length=1, max_length=100, pattern=r"\S")
    owner: str = Field(default="", max_length=100)


class ServiceCreateInput(BusinessServiceCreate):
    source_connection: SourceConnectionInput | None = None


class ServiceUpdateInput(BusinessServiceUpdate):
    source_connection: SourceConnectionInput | None = None


class ApplicationResponse(ORMResponse):
    id: UUID
    code: str
    name: str
    owner: str
    purpose: str
    depends_on: list[UUID]
    entrypoint: str
    source_subdir: str
    path_mappings: list[dict]
    git_url: str | None
    git_ref: str
    git_username: str
    svn_url: str | None
    svn_revision: str
    svn_username: str
    local_path: str | None
    enabled: bool
    has_git_token: bool = False
    has_svn_password: bool = False
    created_at: AwareDatetime
    updated_at: AwareDatetime


class SyncInput(RequestModel):
    source: Literal["auto", "git", "svn", "local"] = "auto"
    expected_commit: Commit | None = None
    expected_revision: Revision | None = None

    @model_validator(mode="after")
    def validate_source_version(self):
        if self.expected_commit and (self.expected_revision or self.source in ("svn", "local")):
            raise ValueError("Git commit requires a Git source")
        if self.expected_revision and self.source in ("git", "local"):
            raise ValueError("SVN revision requires an SVN source")
        return self


class SnapshotResponse(ORMResponse):
    id: UUID
    application_id: UUID
    requested_by: UUID
    status: str
    source: str | None
    git_commit: str | None
    svn_revision: str | None
    content_hash: str | None
    data_gaps: list
    file_count: int
    total_bytes: int
    started_at: AwareDatetime | None
    finished_at: AwareDatetime | None
    error_code: str | None
    created_at: AwareDatetime
    updated_at: AwareDatetime


class ReleaseInput(RequestModel):
    version: str = Field(min_length=1, max_length=100, pattern=r"\S")
    snapshot_id: UUID | None = None
    expected_commit: Commit | None = None
    expected_revision: Revision | None = None
    image_ref: str = Field(default="", max_length=500)
    image_id: ImageID | None = None
    repo_digest: str | None = Field(default=None, max_length=1000)
    change_summary: str = Field(default="", max_length=20000)
    test_result: str = Field(default="", max_length=20000)
    build_source_status: Literal["declared", "clean_git", "local_snapshot"] = "declared"

    @model_validator(mode="after")
    def validate_source_version(self):
        if self.expected_revision and (
            self.expected_commit or self.build_source_status == "clean_git"
        ):
            raise ValueError("SVN revision cannot be combined with Git build evidence")
        return self

    @field_validator("image_ref")
    @classmethod
    def image_reference(cls, value):
        if value and ("://" in value or any(ord(c) < 33 for c in value)):
            raise ValueError("Use a Docker image reference without URL credentials")
        if "@" in value and not re.fullmatch(r"[^@]+@sha256:[0-9a-f]{64}", value):
            raise ValueError("Image reference cannot contain login credentials")
        return value

    @field_validator("repo_digest")
    @classmethod
    def digest(cls, value):
        if value and not re.fullmatch(r"[^\s@]+@sha256:[0-9a-f]{64}", value):
            raise ValueError("RepoDigest must be repository@sha256:digest")
        return value


class ReleaseResponse(ORMResponse):
    id: UUID
    application_id: UUID
    version: str
    snapshot_id: UUID | None
    expected_commit: str | None
    expected_revision: str | None
    image_ref: str
    image_id: str | None
    repo_digest: str | None
    change_summary: str
    test_result: str
    build_source_status: str
    created_at: AwareDatetime
    updated_at: AwareDatetime


class DeploymentInput(RequestModel):
    release_id: UUID
    container_id: UUID | None = None
    llm_endpoint_id: UUID | None = None
    environment: str = Field(min_length=1, max_length=50)
    compose_project: str = Field(default="", max_length=200)
    compose_service: str = Field(default="", max_length=200)
    config_version: str = Field(default="", max_length=200)
    started_at: AwareDatetime
    ended_at: AwareDatetime | None = None

    @model_validator(mode="after")
    def target(self):
        if (self.container_id is None) == (self.llm_endpoint_id is None):
            raise ValueError("Choose exactly one container or LLM Endpoint")
        if self.ended_at and self.ended_at <= self.started_at:
            raise ValueError("End time must be after start time")
        return self


class DeploymentEnd(RequestModel):
    ended_at: AwareDatetime


class DeploymentResponse(ORMResponse):
    id: UUID
    application_id: UUID
    release_id: UUID
    server_id: UUID | None
    container_id: UUID | None
    llm_endpoint_id: UUID | None
    environment: str
    compose_project: str
    compose_service: str
    config_version: str
    started_at: AwareDatetime
    ended_at: AwareDatetime | None
    binding: dict
    created_at: AwareDatetime
    updated_at: AwareDatetime
