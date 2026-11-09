from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic.networks import HttpUrl


class MarketContent(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    category_id: UUID
    title: str = Field(min_length=1, max_length=100)
    summary: str = Field(min_length=1, max_length=300)
    tags: list[str] = Field(default_factory=list, max_length=8)
    description: str = Field(default="", max_length=20000)
    usage: str = Field(default="", max_length=10000)
    author: str = Field(default="", max_length=150)
    source_url: str = Field(default="", max_length=2000)
    target_url: str = Field(default="", max_length=2000)
    platform: str = Field(default="", max_length=200)
    skill_version: str = Field(default="", max_length=100)
    requirements: str = Field(default="", max_length=2000)
    license: str = Field(default="", max_length=100)
    pricing: Literal["unknown", "free", "freemium", "paid", "open_source"] = "unknown"
    reason: str = Field(default="", max_length=2000)

    @field_validator("source_url", "target_url")
    @classmethod
    def http_link(cls, value):
        if value:
            url = HttpUrl(value)
            if url.username or url.password:
                raise ValueError("链接不能包含账号或密码")
            return str(url)
        return value

    @field_validator("tags")
    @classmethod
    def clean_tags(cls, value):
        tags = list(dict.fromkeys(tag.strip() for tag in value if tag.strip()))
        if any(len(tag) > 30 for tag in tags):
            raise ValueError("每个标签最多 30 个字符")
        return tags


class MarketCreate(MarketContent):
    kind: Literal["skill", "tool"]


class MarketDraft(MarketContent):
    base_revision_id: UUID
    expected_version: int = Field(ge=1)


class RevisionAction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: int = Field(ge=1)


class ReviewAction(RevisionAction):
    decision: Literal["approve", "reject"]
    note: str = Field(default="", max_length=2000)


class WithdrawEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    note: str = Field(min_length=1, max_length=2000)


class CategoryInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=60)
    description: str = Field(default="", max_length=300)
    sort_order: int = Field(default=0, ge=0, le=10000)
    enabled: bool = True
    expected_updated_at: str | None = None
