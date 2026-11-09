import asyncio
import json
import logging
import time
from contextlib import contextmanager
from datetime import UTC, datetime
from threading import Lock, RLock

import httpx2
from cryptography.fernet import InvalidToken
from fastapi import HTTPException
from prometheus_client import CollectorRegistry, Counter, Histogram, generate_latest
from prometheus_client.core import GaugeMetricFamily
from sqlalchemy.exc import SQLAlchemyError

import app.repositories.llm.probes as _repository
from app.core.secrets import cipher
from app.db.session import SessionLocal
from app.repositories.agent_config import probe_credential
from app.schemas.llm import ProbeResult

logger = logging.getLogger(__name__)
ERROR_TYPES = (
    "authentication_failed",
    "insufficient_balance",
    "rate_limited",
    "service_error",
    "timeout",
    "connection_failed",
    "request_error",
    "invalid_response",
    "credential_error",
)
PROBE_TIMEOUT = 15
SCAN_INTERVAL = 5


def load_endpoint(endpoint_id):
    with SessionLocal() as db:
        endpoint = _repository.load_endpoint_query(db, endpoint_id)
        if endpoint is None or endpoint.deleted_at is not None:
            raise HTTPException(404, "Resource not found")
        return endpoint


def save_result(result):
    from app.services.operations.usage import record_probe

    with SessionLocal() as db:
        endpoint = _repository.save_result_query(db, result)
        endpoint.last_probe_at = result.checked_at
        endpoint.last_probe_success = result.success
        endpoint.last_error_type = result.error_type
        endpoint.last_http_status = result.http_status
        endpoint.last_latency_ms = result.latency_ms
        record_probe(db, endpoint, result)
        db.commit()


def managed_key(endpoint):
    with SessionLocal() as db:
        return probe_credential(db, endpoint.agent_channel_id, endpoint.base_url)


async def infer(endpoint):
    started = time.perf_counter()
    status, error, input_tokens, output_tokens = None, None, None, None
    try:
        encrypted_key = endpoint.encrypted_api_key
        if endpoint.agent_channel_id is not None:
            encrypted_key = await asyncio.to_thread(managed_key, endpoint)
        if not encrypted_key:
            raise ValueError("credential_unavailable")
        key = cipher().decrypt(encrypted_key.encode()).decode()
    except (InvalidToken, UnicodeError, ValueError, HTTPException):
        error = "credential_error"
    else:
        try:
            # The deadline covers the entire exchange, including a slowly streaming body.
            async with asyncio.timeout(PROBE_TIMEOUT):
                async with httpx2.AsyncClient(
                    timeout=PROBE_TIMEOUT, follow_redirects=False, trust_env=False
                ) as client:
                    async with client.stream(
                        "POST",
                        endpoint.base_url + "/chat/completions",
                        headers={"Authorization": f"Bearer {key}"},
                        json={
                            "model": endpoint.model,
                            "messages": [{"role": "user", "content": "Return only OK."}],
                            "max_tokens": 5,
                            "stream": False,
                            "thinking": {"type": "disabled"},
                        },
                    ) as response:
                        status = response.status_code
                        if status != 200:
                            error = {
                                401: "authentication_failed",
                                402: "insufficient_balance",
                                429: "rate_limited",
                            }.get(
                                status, "service_error" if 500 <= status < 600 else "request_error"
                            )
                        else:
                            raw = bytearray()
                            async for chunk in response.aiter_bytes():
                                raw.extend(chunk)
                                if len(raw) > 1024 * 1024:
                                    raise ValueError
                            payload = json.loads(raw)
                            content = payload["choices"][0]["message"]["content"]
                            if not isinstance(content, str) or content.strip() != "OK":
                                raise ValueError
                            usage = payload.get("usage") or {}
                            for field in ("prompt_tokens", "completion_tokens"):
                                value = usage.get(field)
                                if value is not None and (type(value) is not int or value < 0):
                                    raise ValueError
                            input_tokens = usage.get("prompt_tokens")
                            output_tokens = usage.get("completion_tokens")
        except (TimeoutError, httpx2.TimeoutException):
            error = "timeout"
        except httpx2.HTTPError:
            error = "connection_failed"
        except (ValueError, KeyError, IndexError, TypeError, AttributeError):
            error = "invalid_response"
    return ProbeResult(
        endpoint_id=endpoint.id,
        checked_at=datetime.now(UTC),
        success=error is None,
        error_type=error,
        http_status=status,
        latency_ms=round((time.perf_counter() - started) * 1000, 2),
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )


class ProbeService:
    """One instance per single-process API lifespan; no network calls on scrape."""

    def __init__(self):
        self.locks = {}
        self.semaphore = asyncio.Semaphore(4)
        self.tasks = set()
        self.registry = CollectorRegistry()
        self.requests = Counter(
            "llm_probe_requests_total",
            "Completed probes",
            ["endpoint_id", "outcome"],
            registry=self.registry,
        )
        self.duration = Histogram(
            "llm_request_duration_seconds",
            "Successful inference duration",
            ["endpoint_id"],
            buckets=(0.1, 0.25, 0.5, 1, 2, 3, 5, 8, 10, 15, 30),
            registry=self.registry,
        )
        self.tokens = Counter(
            "llm_tokens_total",
            "Reported token usage",
            ["endpoint_id", "kind"],
            registry=self.registry,
        )
        self.initialized = set()
        self.metrics_lock = RLock()

    @contextmanager
    def exclusive(self, endpoint_id):
        lock = self.locks.setdefault(endpoint_id, Lock())
        if not lock.acquire(blocking=False):
            raise HTTPException(409, "Endpoint probe or update is in progress")
        try:
            yield
        finally:
            lock.release()

    def initialize(self, endpoint_id):
        with self.metrics_lock:
            self._initialize(endpoint_id)

    def _initialize(self, endpoint_id):
        key = str(endpoint_id)
        if key in self.initialized:
            return
        for outcome in ("success", *ERROR_TYPES):
            self.requests.labels(key, outcome)
        self.duration.labels(key)
        for kind in ("input", "output"):
            self.tokens.labels(key, kind)
        self.initialized.add(key)

    def forget(self, endpoint_id):
        with self.metrics_lock:
            self._forget(endpoint_id)

    def _forget(self, endpoint_id):
        key = str(endpoint_id)
        if key not in self.initialized:
            return
        for outcome in ("success", *ERROR_TYPES):
            self.requests.remove(key, outcome)
        self.duration.remove(key)
        for kind in ("input", "output"):
            self.tokens.remove(key, kind)
        self.initialized.remove(key)

    async def run(self, endpoint_id, scheduled=False, authorize=None):
        with self.exclusive(endpoint_id):
            async with self.semaphore:
                if authorize:
                    await asyncio.to_thread(authorize)
                endpoint = await asyncio.to_thread(load_endpoint, endpoint_id)
                if scheduled:
                    if not endpoint.probe_enabled:
                        return None
                    if (
                        endpoint.last_probe_at
                        and (datetime.now(UTC) - endpoint.last_probe_at).total_seconds()
                        < endpoint.probe_interval_seconds
                    ):
                        return None
                if endpoint.probe_enabled:
                    self.initialize(endpoint.id)
                result = await infer(endpoint)
                if authorize:
                    await asyncio.to_thread(authorize)
                await asyncio.to_thread(save_result, result)
                if endpoint.probe_enabled:
                    key = str(endpoint.id)
                    self.requests.labels(key, result.error_type or "success").inc()
                    if result.success:
                        self.duration.labels(key).observe(result.latency_ms / 1000)
                    for kind, count in (
                        ("input", result.input_tokens),
                        ("output", result.output_tokens),
                    ):
                        if count is not None:
                            self.tokens.labels(key, kind).inc(count)
                return result

    async def scheduled_run(self, endpoint_id):
        try:
            await self.run(endpoint_id, scheduled=True)
        except HTTPException as exc:
            if exc.status_code not in (404, 409):
                logger.error("LLM probe failed (%s)", type(exc).__name__)
        except SQLAlchemyError as exc:
            logger.error("LLM probe database failed (%s)", type(exc).__name__)

    async def schedule_once(self):
        def endpoints():
            with SessionLocal() as db:
                return _repository.ProbeService_schedule_once_endpoints_query(db).all()

        active = await asyncio.to_thread(endpoints)
        now = datetime.now(UTC)
        for endpoint in active:
            self.initialize(endpoint.id)
            if len(self.tasks) >= 4:
                break
            if (
                endpoint.last_probe_at
                and (now - endpoint.last_probe_at).total_seconds() < endpoint.probe_interval_seconds
            ):
                continue
            if self.locks.get(endpoint.id) and self.locks[endpoint.id].locked():
                continue
            task = asyncio.create_task(self.scheduled_run(endpoint.id))
            self.tasks.add(task)
            task.add_done_callback(self.tasks.discard)

    async def scheduler(self):
        while True:
            try:
                await self.schedule_once()
            except SQLAlchemyError as exc:
                logger.error("LLM scheduler database failed (%s)", type(exc).__name__)
            await asyncio.sleep(SCAN_INTERVAL)

    async def close(self):
        tasks = list(self.tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    def metrics(self, db):
        active = _repository.ProbeService_metrics_query(db)
        enabled = {str(e.id) for e in active}
        for key in list(self.initialized - enabled):
            self.forget(key)
        for endpoint in active:
            self.initialize(endpoint.id)

        class SnapshotCollector:
            def collect(self):
                definitions = (
                    ("llm_probe_success", "Last inference success"),
                    ("llm_http_status", "Last HTTP status; zero when unavailable"),
                    ("llm_probe_latency_seconds", "Last probe duration"),
                    ("llm_probe_last_run_timestamp_seconds", "Last probe or creation time"),
                    ("llm_probe_interval_seconds", "Configured interval"),
                    ("llm_probe_has_result", "Has completed a probe"),
                )
                gauges = {
                    n: GaugeMetricFamily(n, help_text, labels=["endpoint_id", "source_epoch"])
                    for n, help_text in definitions
                }
                errors = GaugeMetricFamily(
                    "llm_probe_error",
                    "Last error",
                    labels=["endpoint_id", "source_epoch", "error_type"],
                )
                for endpoint in active:
                    key = str(endpoint.id)
                    epoch = endpoint.probe_config_changed_at.isoformat()
                    values = (
                        int(bool(endpoint.last_probe_success)),
                        endpoint.last_http_status or 0,
                        (endpoint.last_latency_ms or 0) / 1000,
                        (endpoint.last_probe_at or endpoint.created_at).timestamp(),
                        endpoint.probe_interval_seconds,
                        int(endpoint.last_probe_at is not None),
                    )
                    for (name, _), value in zip(definitions, values, strict=True):
                        gauges[name].add_metric([key, epoch], value)
                    for error in ERROR_TYPES:
                        errors.add_metric(
                            [key, epoch, error], int(endpoint.last_error_type == error)
                        )
                yield from gauges.values()
                yield errors

        snapshots = CollectorRegistry()
        snapshots.register(SnapshotCollector())
        return generate_latest(self.registry) + generate_latest(snapshots)
