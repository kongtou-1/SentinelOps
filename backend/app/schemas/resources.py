import base64
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, SecretStr, model_validator

from app.schemas.auth import RequestModel

Name = Annotated[str, Field(min_length=1, max_length=100)]
Host = Annotated[str, Field(max_length=253, pattern=r"^[a-zA-Z0-9][a-zA-Z0-9.\-]*$")]
Target = Annotated[str, Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9.\-]*:[0-9]{1,5}$")]
Metric = Literal[
    "host_cpu_percent",
    "host_memory_percent",
    "host_disk_percent",
    "host_load1",
    "host_iowait_percent",
    "host_memory_available_bytes",
    "host_swap_percent",
    "host_disk_available_bytes",
    "host_inode_percent",
    "host_disk_read_bytes",
    "host_disk_write_bytes",
    "host_disk_io_wait_seconds",
    "host_network_receive_errors",
    "host_network_transmit_errors",
    "host_network_receive_drops",
    "host_network_transmit_drops",
    "http_latency_seconds",
    "container_restarts_5m",
    "container_oom_5m",
    "container_unhealthy",
    "host_network_receive_bytes",
    "host_network_transmit_bytes",
    "container_cpu_percent",
    "container_cpu_host_percent",
    "container_cpu_quota_percent",
    "container_memory_limit_bytes",
    "container_memory_bytes",
    "container_network_receive_bytes",
    "container_network_transmit_bytes",
    "target_up",
    "http_up",
    "container_up",
]


class MonitoringSample(BaseModel):
    availability: Literal["available", "unavailable", "unconfigured", "paused", "stale"]
    value: float | None = None
    sampled_at: AwareDatetime | None = None
    reason: str | None = None


class CollectorStatus(MonitoringSample):
    target: str | None = None


class ServerMonitoringSummary(BaseModel):
    server_id: UUID
    metrics: dict[str, MonitoringSample]
    collectors: dict[str, CollectorStatus]


class ContainerHealth(BaseModel):
    status: Literal["healthy", "unhealthy", "starting", "none", "stopped", "unknown"]
    availability: Literal["available", "unavailable", "unconfigured", "paused", "stale"]
    sampled_at: AwareDatetime | None = None
    reason: str | None = None


class ContainerDiagnostic(BaseModel):
    container_id: UUID
    collected_at: AwareDatetime
    state: str | None = None
    logs: str | None = None
    truncated: bool = False
    error_code: str | None = None


class CredentialInput(RequestModel):
    name: Name
    auth_type: Literal["password", "private_key"]
    secret: SecretStr
    passphrase: SecretStr | None = None

    @model_validator(mode="after")
    def validate_secret(self):
        if not 1 <= len(self.secret.get_secret_value()) <= 32768:
            raise ValueError("Secret must contain 1 to 32768 characters")
        if self.auth_type == "password" and self.passphrase is not None:
            raise ValueError("Passphrase is only supported for private keys")
        return self


class ORMResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class CredentialResponse(ORMResponse):
    id: UUID
    name: str
    auth_type: str
    created_at: AwareDatetime
    updated_at: AwareDatetime


class ServerInput(RequestModel):
    name: Name
    host: Host
    ssh_port: int = Field(default=22, ge=1, le=65535)
    ssh_user: str = Field(default="ops", max_length=64, pattern=r"^[a-zA-Z0-9_.\-]+$")
    credential_id: UUID | None = None
    ssh_host_key: str | None = Field(default=None, max_length=8192)
    environment: str = Field(default="test", min_length=1, max_length=50)
    node_exporter_target: Target
    cadvisor_target: Target
    docker_state_target: Target | None = None
    blackbox_exporter_target: Target | None = None
    health_url: str | None = Field(default=None, max_length=2000)
    monitoring_enabled: bool = True

    @model_validator(mode="after")
    def validate_targets(self):
        for target in (
            self.node_exporter_target,
            self.cadvisor_target,
            self.docker_state_target,
            self.blackbox_exporter_target,
        ):
            if target is None:
                continue
            if not 1 <= int(target.rsplit(":", 1)[1]) <= 65535:
                raise ValueError("Exporter port must be between 1 and 65535")
        if self.credential_id is not None and not self.ssh_host_key:
            raise ValueError("SSH credentials require a pinned host public key")
        if self.ssh_host_key:
            parts = self.ssh_host_key.split()
            try:
                if len(parts) < 2 or parts[0] not in (
                    "ssh-ed25519",
                    "ssh-rsa",
                    "ecdsa-sha2-nistp256",
                    "ecdsa-sha2-nistp384",
                    "ecdsa-sha2-nistp521",
                ):
                    raise ValueError
                base64.b64decode(parts[1], validate=True)
            except ValueError:
                raise ValueError("Provide an OpenSSH host public key") from None
        if self.health_url:
            from urllib.parse import urlsplit

            url = urlsplit(self.health_url)
            if url.scheme not in ("http", "https") or not url.hostname or url.username:
                raise ValueError("Health URL must be HTTP(S), without credentials")
        return self


class ServerResponse(ORMResponse):
    id: UUID
    name: str
    host: str
    ssh_port: int
    ssh_user: str
    credential_id: UUID | None
    ssh_host_key: str | None
    environment: str
    node_exporter_target: str
    cadvisor_target: str
    docker_state_target: str | None
    blackbox_exporter_target: str | None
    collection_config_changed_at: AwareDatetime
    health_url: str | None
    monitoring_enabled: bool
    last_ssh_test_at: AwareDatetime | None
    last_ssh_test_status: str | None
    created_at: AwareDatetime
    updated_at: AwareDatetime


class ContainerResponse(ORMResponse):
    id: UUID
    server_id: UUID
    application_id: UUID | None = None
    docker_id: str
    name: str
    image: str
    status: str
    labels: dict
    last_seen_at: AwareDatetime
    image_id: str | None = None
    identity: dict = {}
    identity_observed_at: AwareDatetime | None = None
    service_name: str = ""
    purpose: str = ""
    depends_on: list[UUID] = Field(default_factory=list)
    created_at: AwareDatetime
    updated_at: AwareDatetime


class ContainerServiceInput(RequestModel):
    service_name: str = Field(default="", max_length=100)
    purpose: str = Field(default="", max_length=2000)
    depends_on: list[UUID] = Field(default_factory=list, max_length=100)


class ContainerServiceStatus(BaseModel):
    container_id: UUID
    state: str | None = None
    health: str | None = None
    sampled_at: AwareDatetime | None = None
    networks: list[str] | None = None
    mounts: list[dict] | None = None
    cpu_host_percent: float | None = None
    memory_bytes: float | None = None


class BusinessServiceProfile(RequestModel):
    name: str = Field(min_length=1, max_length=100, pattern=r"\S")
    purpose: str = Field(default="", max_length=2000)
    owner: str = Field(default="", max_length=100)
    depends_on: list[UUID] = Field(default_factory=list, max_length=100)


class BusinessServiceMembers(RequestModel):
    container_ids: list[UUID] = Field(default_factory=list, max_length=2000)


class BusinessServiceUpdate(BusinessServiceProfile, BusinessServiceMembers):
    pass


class BusinessServiceCreate(BusinessServiceUpdate):
    code: str = Field(min_length=1, max_length=100, pattern=r"^[a-z][a-z0-9_.-]*$")


class BusinessServiceResponse(ORMResponse):
    id: UUID
    code: str
    name: str
    purpose: str
    owner: str
    depends_on: list[UUID]
    created_at: AwareDatetime
    updated_at: AwareDatetime


class BusinessServiceGroup(BusinessServiceResponse):
    container_ids: list[UUID]


class ServerContainerOverview(BaseModel):
    server_id: UUID
    collected_at: AwareDatetime
    containers: list[ContainerResponse]
    observations: list[ContainerServiceStatus]
    services: list[BusinessServiceGroup]
    unassigned_container_ids: list[UUID]
    host_cpu_percent: float | None = None
    host_memory_total_bytes: float | None = None
    host_memory_used_bytes: float | None = None


class RuleInput(RequestModel):
    service_monitor_id: UUID | None = None
    auto_diagnose: bool | None = None
    code: str = Field(min_length=1, max_length=100, pattern=r"^[a-zA-Z_][a-zA-Z0-9_]*$")
    server_id: UUID | None = None
    llm_endpoint_id: UUID | None = None
    prediction_task_id: UUID | None = None
    prediction_horizon_hours: Literal[1, 6, 24] | None = None
    metric: (
        Metric
        | Literal[
            "service_probe_up",
            "service_probe_latency_seconds",
            "service_probe_stale",
            "service_error_rate",
            "service_latency_p95_seconds",
            "llm_up",
            "llm_latency_seconds",
            "llm_authentication_failed",
            "llm_insufficient_balance",
            "llm_rate_limited",
            "llm_service_error",
            "llm_probe_stale",
            "prediction_risk",
            "prediction_anomaly",
        ]
    )
    threshold: float = Field(ge=0, allow_inf_nan=False)
    for_seconds: int = Field(default=30, ge=0, le=86400)
    severity: Literal["warning", "critical"] = "warning"
    enabled: bool = True

    @model_validator(mode="after")
    def validate_threshold(self):
        if self.metric.startswith("service_"):
            if not self.service_monitor_id or any(
                (
                    self.server_id,
                    self.llm_endpoint_id,
                    self.prediction_task_id,
                    self.prediction_horizon_hours,
                )
            ):
                raise ValueError("Service rules require exactly one service monitor")
            if self.metric == "service_probe_up" and self.threshold != 1:
                raise ValueError("Availability rules use threshold 1")
            if self.metric == "service_probe_stale" and self.threshold != 0:
                raise ValueError("Stale rules use threshold 0")
            if self.metric == "service_error_rate" and self.threshold > 1:
                raise ValueError("Error rate must not exceed 1")
        elif self.service_monitor_id:
            raise ValueError("Monitor target requires a service metric")
        if self.metric.startswith("prediction_"):
            if not self.prediction_task_id or self.server_id or self.llm_endpoint_id:
                raise ValueError("Prediction rules must target only a prediction task")
            if self.metric == "prediction_risk":
                if self.prediction_horizon_hours is None or self.threshold not in (0, 1):
                    raise ValueError("Risk rules require horizon 1/6/24 and threshold 0 or 1")
            elif self.prediction_horizon_hours is not None or self.threshold != 0:
                raise ValueError("Anomaly rules use threshold 0 and no horizon")
        elif self.prediction_task_id or self.prediction_horizon_hours is not None:
            raise ValueError("Prediction fields are only valid for prediction rules")
        if self.metric.startswith("llm_"):
            if self.server_id is not None:
                raise ValueError("LLM rules cannot target servers")
        elif self.llm_endpoint_id is not None:
            raise ValueError("Host and container rules cannot target LLM endpoints")
        if (
            self.metric in ("target_up", "http_up", "container_up", "llm_up")
            and self.threshold != 1
        ):
            raise ValueError("Availability rules use threshold 1 (fires when below 1)")
        if (
            self.metric
            in (
                "llm_authentication_failed",
                "llm_insufficient_balance",
                "llm_rate_limited",
                "llm_service_error",
                "llm_probe_stale",
            )
            and self.threshold != 0
        ):
            raise ValueError("LLM error and stale flags use threshold 0 (fires when above 0)")
        if self.metric.endswith("_percent"):
            if self.threshold > 100:
                raise ValueError("Host percentage threshold must not exceed 100")
        return self


class RuleResponse(ORMResponse):
    service_monitor_id: UUID | None
    auto_diagnose: bool | None
    id: UUID
    code: str
    server_id: UUID | None
    llm_endpoint_id: UUID | None
    prediction_task_id: UUID | None
    prediction_horizon_hours: int | None
    metric: str
    threshold: float
    for_seconds: int
    severity: str
    enabled: bool
    created_at: AwareDatetime
    updated_at: AwareDatetime


class WebhookAlert(BaseModel):
    status: Literal["firing", "resolved"]
    labels: dict[str, str]
    annotations: dict[str, str] = Field(default_factory=dict)
    startsAt: AwareDatetime
    endsAt: AwareDatetime
    fingerprint: str = Field(min_length=1, max_length=64, pattern=r"^[a-fA-F0-9]+$")

    @model_validator(mode="after")
    def valid_dates(self):
        if self.status == "resolved" and self.endsAt < self.startsAt:
            raise ValueError("Resolved alert end must not precede its start")
        if len(self.labels) > 100 or len(self.annotations) > 100:
            raise ValueError("Too many labels or annotations")
        if any(
            len(k) > 256 or len(v) > 8192
            for d in (self.labels, self.annotations)
            for k, v in d.items()
        ):
            raise ValueError("Label or annotation too long")
        return self


class WebhookPayload(BaseModel):
    version: Literal["4"] = "4"
    alerts: list[WebhookAlert] = Field(max_length=1000)


class AlertResponse(ORMResponse):
    service_monitor_id: UUID | None
    id: UUID
    incident_id: UUID | None
    fingerprint: str
    rule_id: UUID | None
    server_id: UUID | None
    llm_endpoint_id: UUID | None
    prediction_task_id: UUID | None
    severity: str
    status: str
    starts_at: AwareDatetime
    ends_at: AwareDatetime | None
    labels: dict
    annotations: dict
    rule_snapshot: dict
    recovery_status: str
    recovery_reason: str | None
    recovery_checked_at: AwareDatetime | None
    recovery_evidence_at: AwareDatetime | None
    stability_started_at: AwareDatetime | None = None
    stability_last_evidence_at: AwareDatetime | None = None
    acknowledged_at: AwareDatetime | None
    acknowledged_by: UUID | None
    comment: str | None
    created_at: AwareDatetime
    updated_at: AwareDatetime


class AcknowledgeInput(RequestModel):
    comment: str = Field(min_length=1, max_length=2000)
