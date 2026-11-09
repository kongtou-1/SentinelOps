from typing import Literal
from uuid import UUID

from pydantic import Field

from app.schemas.auth import RequestModel
from app.schemas.monitoring import ServiceMetric
from app.schemas.resources import Metric


class Empty(RequestModel):
    pass


class Asset(RequestModel):
    asset_id: UUID


class MetricInput(Asset):
    metric: Metric
    detail: Literal["summary", "raw"] = Field(
        default="summary",
        description="summary 返回每条曲线的首末值、极值及采样数量；仅需完整时序时用 raw。",
    )


class LLMMetricInput(Asset):
    metric: str = Field(pattern=r"^llm_(success_rate|latency_avg_seconds|latency_p95_seconds)$")


class ServiceMetricInput(Asset):
    metric: ServiceMetric


class SearchInput(RequestModel):
    query: str = Field(min_length=1, max_length=1000)
    tag: str | None = Field(default=None, max_length=50)


class SkillInput(RequestModel):
    skill_id: UUID
    version: int = Field(ge=1)
