from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, Field, model_validator

from app.schemas.auth import RequestModel
from app.schemas.resources import ORMResponse

PredictionMetric = Literal[
    "host_cpu_percent",
    "host_memory_percent",
    "host_disk_percent",
    "host_network_receive_bytes",
    "host_network_transmit_bytes",
    "container_cpu_percent",
    "container_memory_bytes",
    "container_network_receive_bytes",
    "container_network_transmit_bytes",
    "llm_latency_avg_seconds",
    "llm_latency_p95_seconds",
    "llm_error_rate",
]


class TaskInput(RequestModel):
    name: str = Field(min_length=1, max_length=100)
    server_id: UUID | None = None
    container_id: UUID | None = None
    llm_endpoint_id: UUID | None = None
    metric: PredictionMetric
    selector: dict[str, str] = Field(default_factory=dict)
    threshold: float = Field(ge=0, allow_inf_nan=False)
    enabled: bool = False
    interval_seconds: int = Field(default=300, ge=300, le=86400)

    @model_validator(mode="after")
    def valid_resource(self):
        if (
            sum(v is not None for v in (self.server_id, self.container_id, self.llm_endpoint_id))
            != 1
        ):
            raise ValueError("Choose exactly one server, container or LLM endpoint")
        expected = "host_" if self.server_id else "container_" if self.container_id else "llm_"
        if not self.metric.startswith(expected):
            raise ValueError("Metric does not match resource type")
        required = (
            set(("device", "mountpoint", "fstype"))
            if self.metric == "host_disk_percent"
            else {"device"}
            if self.metric.startswith("host_network_")
            else {"interface"}
            if self.metric.startswith("container_network_")
            else set()
        )
        if set(self.selector) != required:
            raise ValueError("Selector must contain exactly: " + ", ".join(sorted(required)))
        if any(not v or len(v) > 256 for v in self.selector.values()):
            raise ValueError("Invalid series selector")
        ceiling = (
            100
            if self.metric in ("host_cpu_percent", "host_memory_percent", "host_disk_percent")
            else 1
            if self.metric == "llm_error_rate"
            else None
        )
        if ceiling is not None and self.threshold > ceiling:
            raise ValueError("Threshold exceeds metric bounds")
        return self


class TaskResponse(ORMResponse):
    id: UUID
    name: str
    server_id: UUID | None
    container_id: UUID | None
    llm_endpoint_id: UUID | None
    metric: str
    selector: dict
    threshold: float
    enabled: bool
    interval_seconds: int
    next_run_at: AwareDatetime
    created_at: AwareDatetime
    updated_at: AwareDatetime
    latest_result: dict | None = None


class ResultResponse(ORMResponse):
    id: UUID
    task_id: UUID
    status: str
    trigger: str
    started_at: AwareDatetime | None
    finished_at: AwareDatetime | None
    data_end_at: AwareDatetime | None
    error_code: str | None
    config: dict
    quality: dict
    horizons: dict
    anomalies: list
    created_at: AwareDatetime
    updated_at: AwareDatetime
    fresh: bool = False
