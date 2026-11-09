"""Bounded, fixed-protocol probes; no shell and no response bodies in evidence."""

import asyncio
import hashlib
import json
import math
import socket
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime

import httpx2
from cryptography.fernet import InvalidToken
from fastapi import HTTPException
from prometheus_client.parser import text_string_to_metric_families

from app.core.config import get_settings
from app.core.secrets import cipher
from app.integrations.service_token_auth import TokenAuthError, TokenSession


@dataclass
class Observation:
    checked_at: datetime
    success: bool | None
    latency_seconds: float | None
    error_code: str | None = None
    http_status: int | None = None
    evidence: dict = field(default_factory=dict)


def module_name(monitor):
    digest = hashlib.sha256(
        (str(monitor.id) + monitor.config_changed_at.isoformat()).encode()
    ).hexdigest()[:16]
    return "service_" + digest


def request_config(monitor):
    return (
        json.loads(cipher().decrypt(monitor.encrypted_request.encode()))
        if monitor.encrypted_request
        else {}
    )


def assertion_value(payload, path):
    for part in path.split("."):
        payload = (
            payload[int(part)] if isinstance(payload, list) and part.isdigit() else payload[part]
        )
    return payload


async def http_probe(monitor, token_session=None):
    config = request_config(monitor)
    headers = {**config.get("headers", {}), "X-SentinelOps-Probe": "1"}
    async with httpx2.AsyncClient(
        timeout=monitor.timeout_seconds, follow_redirects=False, trust_env=False
    ) as client:
        auth = config.get("token_auth")
        if auth:
            session = token_session if token_session is not None else TokenSession()
            # An automatic token replaces any manually configured Authorization header.
            headers = {k: v for k, v in headers.items() if k.lower() != "authorization"}
            headers["Authorization"] = "Bearer " + await session.token(client, auth)
            for attempt in range(2):
                result = await http_response(client, monitor, config, headers)
                if result[2] != 401:
                    return result
                if attempt == 0:
                    headers["Authorization"] = "Bearer " + await session.token(
                        client, auth, force=True
                    )
            session.clear()
            return False, "auth_rejected", 401, {}
        return await http_response(client, monitor, config, headers)


async def http_response(client, monitor, config, headers):
    async with client.stream(
        monitor.method, monitor.target, headers=headers, json=config.get("body")
    ) as response:
        evidence = {"expected_status": monitor.expected_status}
        if response.status_code != monitor.expected_status:
            return False, "unexpected_status", response.status_code, evidence
        raw = bytearray()
        async for chunk in response.aiter_bytes():
            raw.extend(chunk)
            if len(raw) > 1024 * 1024:
                return False, "response_too_large", response.status_code, evidence
        evidence["response_sha256"] = hashlib.sha256(raw).hexdigest()
        if monitor.assertions:
            try:
                payload = json.loads(raw)
            except ValueError:
                return False, "invalid_json", response.status_code, evidence
            for path, expected in monitor.assertions.items():
                try:
                    actual = assertion_value(payload, path)
                except (KeyError, TypeError, IndexError, ValueError):
                    actual = object()
                if type(actual) is not type(expected) or actual != expected:
                    return (
                        False,
                        "assertion_failed",
                        response.status_code,
                        {**evidence, "assertion_path": path},
                    )
        return True, None, response.status_code, evidence


async def blackbox_probe(monitor, server):
    if server and (not server.monitoring_enabled or not server.blackbox_exporter_target):
        return None, None, "collector_unavailable"
    url = "http://" + server.blackbox_exporter_target if server else get_settings().blackbox_url
    target = monitor.dns_server if monitor.protocol == "dns" else monitor.target
    module = module_name(monitor) if server else "icmp"
    async with httpx2.AsyncClient(
        timeout=monitor.timeout_seconds + 2, follow_redirects=False, trust_env=False
    ) as client:
        async with client.stream(
            "GET", url.rstrip("/") + "/probe", params={"module": module, "target": target}
        ) as response:
            if response.status_code != 200:
                return None, None, "collector_unavailable"
            raw = bytearray()
            async for chunk in response.aiter_bytes():
                raw.extend(chunk)
                if len(raw) > 128 * 1024:
                    return None, None, "collector_invalid_response"
    values = {
        s.name: s.value
        for f in text_string_to_metric_families(raw.decode())
        for s in f.samples
        if not s.labels
    }
    success, duration = values.get("probe_success"), values.get("probe_duration_seconds")
    if success not in (0, 1) or duration is None or not math.isfinite(duration) or duration < 0:
        return None, None, "collector_invalid_response"
    return bool(success), duration, None if success else "target_unavailable"


async def probe(monitor, server=None, token_session=None):
    started = time.perf_counter()
    remote = monitor.probe_server_id is not None or monitor.protocol == "icmp"
    status, evidence = None, {}
    try:
        async with asyncio.timeout(monitor.timeout_seconds + (2 if remote else 0)):
            if remote:
                success, duration, error = await blackbox_probe(monitor, server)
                return Observation(
                    datetime.now(UTC),
                    success,
                    duration,
                    error,
                    evidence={
                        "source": str(monitor.probe_server_id) if server else "platform_blackbox"
                    },
                )
            if monitor.protocol == "http":
                success, error, status, evidence = await http_probe(monitor, token_session)
            elif monitor.protocol == "tcp":
                host, port = monitor.target.rsplit(":", 1)
                _, writer = await asyncio.open_connection(host, int(port))
                writer.close()
                await writer.wait_closed()
                success, error = True, None
            else:
                await asyncio.get_running_loop().getaddrinfo(
                    monitor.target, None, type=socket.SOCK_STREAM
                )
                success, error = True, None
    except TokenAuthError as exc:
        success, error, status = False, exc.code, exc.http_status
    except (TimeoutError, httpx2.TimeoutException):
        success, error = (None, "collector_unavailable") if remote else (False, "timeout")
    except (httpx2.HTTPError, OSError):
        success, error = (None, "collector_unavailable") if remote else (False, "connection_failed")
    except (InvalidToken, HTTPException):
        success, error = None, "credential_unavailable"
    except (ValueError, UnicodeError):
        success, error = (
            (None, "collector_invalid_response") if remote else (False, "invalid_response")
        )
    return Observation(
        datetime.now(UTC),
        success,
        time.perf_counter() - started if success is not None else None,
        error,
        status,
        evidence,
    )
