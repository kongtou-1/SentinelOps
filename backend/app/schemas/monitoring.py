import json
import re
from typing import Literal
from urllib.parse import parse_qsl, urlsplit
from uuid import UUID

from pydantic import AwareDatetime, Field, model_validator

from app.schemas.auth import RequestModel
from app.schemas.resources import ContainerServiceStatus, ORMResponse, Target

ServiceMetric = Literal[
    "service_probe_up",
    "service_probe_latency_seconds",
    "service_probe_success_rate",
    "service_probe_stale",
    "service_request_rate",
    "service_error_rate",
    "service_latency_p95_seconds",
    "service_request_count",
]


class MonitorTokenAuth(RequestModel):
    login_url: str = Field(min_length=1, max_length=2000)
    login_body: dict = Field(default_factory=dict)
    headers: dict[str, str] = Field(default_factory=dict)
    refresh_url: str = Field(default="", max_length=2000)
    refresh_body: dict = Field(default_factory=dict)
    refresh_token_field: str = Field(default="refresh_token", pattern=r"^[A-Za-z0-9_-]{1,100}$")
    access_token_path: str = "access_token"
    refresh_token_path: str = "refresh_token"
    expires_in_path: str = "expires_in"
    default_expires_in: int = Field(default=300, ge=1, le=86400)

    @model_validator(mode="after")
    def valid_auth(self):
        for value in (self.login_url, self.refresh_url):
            if not value:
                continue
            url = urlsplit(value)
            if (
                url.scheme not in {"http", "https"}
                or not url.hostname
                or any((url.username, url.password, url.query, url.fragment))
            ):
                raise ValueError("Authentication URLs must be HTTP(S) without credentials or query")
            _ = url.port
        for path in (self.access_token_path, self.refresh_token_path, self.expires_in_path):
            if len(path) > 200 or not re.fullmatch(r"[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)*", path):
                raise ValueError("Use simple dotted authentication response paths")
        for key, value in self.headers.items():
            if (
                not re.fullmatch(r"[A-Za-z0-9-]{1,100}", key)
                or any(ord(c) < 32 or ord(c) > 126 for c in value)
                or key.lower()
                in {"host", "content-length", "transfer-encoding", "x-sentinelops-probe"}
            ):
                raise ValueError("Invalid authentication header")
        if len(json.dumps(self.model_dump(), allow_nan=False).encode()) > 16384:
            raise ValueError("Authentication configuration is too large")
        return self


class MonitorInput(RequestModel):
    name: str = Field(min_length=1, max_length=100, pattern=r"\S")
    protocol: Literal["http", "tcp", "dns", "icmp"] = "http"
    target: str = Field(min_length=1, max_length=2000)
    method: Literal["GET", "HEAD", "POST"] = "GET"
    route: str = Field(default="", max_length=200, pattern=r"^(/[A-Za-z0-9_./:{}-]*)?$")
    dns_server: str = Field(default="", max_length=300)
    container_id: UUID | None = None
    probe_server_id: UUID | None = None
    importance: Literal["critical", "optional"] = "critical"
    expected_status: int = Field(default=200, ge=100, le=599)
    assertions: dict[str, str | bool | int | float | None] = Field(
        default_factory=dict, max_length=16
    )
    headers: dict[str, str] | None = None
    request_body: dict | None = None
    token_auth: MonitorTokenAuth | Literal[False] | None = None
    safe_post: bool = False
    interval_seconds: int = Field(default=60, ge=15, le=3600)
    timeout_seconds: int = Field(default=5, ge=1, le=30)
    enabled: bool = True

    @model_validator(mode="after")
    def valid_target(self):
        if self.timeout_seconds >= self.interval_seconds:
            raise ValueError("Timeout must be shorter than the interval")
        host_port = r"[A-Za-z0-9_.-]+:([0-9]{1,5})"

        def valid_port(value):
            match = re.fullmatch(host_port, value)
            return match and 1 <= int(match[1]) <= 65535

        if self.protocol == "http":
            url = urlsplit(self.target)
            if (
                url.scheme not in ("http", "https")
                or not url.hostname
                or url.username
                or url.fragment
            ):
                raise ValueError("Use an HTTP(S) URL without credentials or a fragment")
            _ = url.port
            if any(
                k.lower()
                in {"key", "token", "api_key", "access_token", "password", "authorization"}
                for k, _ in parse_qsl(url.query)
            ):
                raise ValueError("Put credentials in encrypted headers")
        elif self.protocol == "tcp":
            if not valid_port(self.target):
                raise ValueError("TCP target must be host:port")
        elif not re.fullmatch(r"[A-Za-z0-9_.-]{1,253}", self.target):
            raise ValueError("DNS/ICMP target must be a hostname or IPv4 address")
        if self.protocol != "http" and (
            self.method != "GET"
            or self.headers
            or self.request_body
            or self.token_auth
            or self.assertions
            or self.route
        ):
            raise ValueError("HTTP request fields require HTTP protocol")
        if self.protocol == "dns" and self.probe_server_id and not valid_port(self.dns_server):
            raise ValueError("Host DNS probes require a DNS server in host:port format")
        if self.dns_server and not (self.protocol == "dns" and self.probe_server_id):
            raise ValueError("DNS server is only used by host DNS probes")
        if self.method == "POST" and not self.safe_post:
            raise ValueError("Confirm that POST is a safe probe request")
        if self.method != "POST" and self.request_body:
            raise ValueError("A request body requires POST")
        if self.probe_server_id and (
            self.method == "POST"
            or self.headers
            or self.request_body
            or self.assertions
            or self.token_auth
        ):
            raise ValueError(
                "Host probes support GET/HEAD status checks; "
                "use platform probes for encrypted requests and JSON assertions"
            )
        for key in self.assertions:
            if not re.fullmatch(r"[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)*", key) or len(key) > 200:
                raise ValueError("Use simple dotted JSON assertion paths")
        if any(
            not re.fullmatch(r"[A-Za-z0-9-]{1,100}", k) or "\n" in v or "\r" in v
            for k, v in (self.headers or {}).items()
        ):
            raise ValueError("Invalid HTTP header")
        if any(
            k.lower() in {"host", "content-length", "transfer-encoding", "x-sentinelops-probe"}
            for k in self.headers or {}
        ):
            raise ValueError("Reserved HTTP header")
        if (
            len(
                json.dumps(
                    {
                        "headers": self.headers,
                        "body": self.request_body,
                        "assertions": self.assertions,
                    },
                    allow_nan=False,
                ).encode()
            )
            > 16384
        ):
            raise ValueError("Request configuration is too large")
        return self


class MonitorTestInput(MonitorInput):
    monitor_id: UUID | None = None


class MonitorTestResponse(RequestModel):
    success: bool | None
    error_code: str | None
    http_status: int | None
    latency_seconds: float | None
    refresh_tested: bool = False


class MonitorResponse(ORMResponse):
    id: UUID
    application_id: UUID
    container_id: UUID | None
    probe_server_id: UUID | None
    name: str
    protocol: str
    target: str
    method: str
    route: str
    dns_server: str
    importance: str
    expected_status: int
    assertions: dict
    request_configured: bool
    token_auth_configured: bool
    interval_seconds: int
    timeout_seconds: int
    enabled: bool
    config_changed_at: AwareDatetime
    last_probe_at: AwareDatetime | None
    last_probe_success: bool | None
    last_error_code: str | None
    last_http_status: int | None
    last_latency_seconds: float | None
    last_success_at: AwareDatetime | None
    last_failure_at: AwareDatetime | None
    last_evidence: dict
    status: str
    created_at: AwareDatetime
    updated_at: AwareDatetime


class TrafficInput(RequestModel):
    traffic_target: Target | None = None

    @model_validator(mode="after")
    def valid_port(self):
        if self.traffic_target and not 1 <= int(self.traffic_target.rsplit(":", 1)[1]) <= 65535:
            raise ValueError("Invalid traffic collector port")
        return self


class RuntimeInstance(ContainerServiceStatus):
    id: UUID
    name: str
    server_id: UUID
    server_name: str


class RelatedService(RequestModel):
    id: UUID
    name: str
    status: str


class RelatedAlert(RequestModel):
    id: UUID
    name: str
    severity: str
    status: str


class RelatedIncident(RequestModel):
    id: UUID
    title: str
    severity: str
    status: str


class ObservabilityResponse(RequestModel):
    application_id: UUID
    name: str
    business_status: str
    traffic_target: str | None
    monitors: list[MonitorResponse]
    containers: list[RuntimeInstance]
    dependencies: list[RelatedService]
    dependents: list[RelatedService]
    llm_endpoints: list[RelatedService]
    alerts: list[RelatedAlert]
    incidents: list[RelatedIncident]
