import asyncio
import logging
import random
import time
from contextlib import contextmanager
from datetime import UTC, datetime
from threading import Lock

from fastapi import HTTPException
from prometheus_client import CollectorRegistry, Counter, Histogram, generate_latest
from prometheus_client.core import GaugeMetricFamily

import app.repositories.monitoring.service_monitors as repository
from app.db.session import SessionLocal
from app.integrations.service_probes import probe
from app.integrations.service_token_auth import TokenSession
from app.models import Application

logger = logging.getLogger(__name__)
LABELS = ("monitor_id", "application_id", "source_epoch")


def status(row, now=None):
    if not row.enabled:
        return "paused"
    now = now or datetime.now(UTC)
    if (
        now.timestamp() - (row.last_probe_at or row.config_changed_at).timestamp()
        > 2 * row.interval_seconds
    ):
        return "stale"
    return (
        "unknown"
        if row.last_probe_success is None
        else "healthy"
        if row.last_probe_success
        else "failed"
    )


class ServiceProbes:
    def __init__(self):
        self.locks, self.due, self.epochs = {}, {}, {}
        self.token_sessions = {}
        self.tasks = set()
        self.semaphore = asyncio.Semaphore(4)
        self.registry = CollectorRegistry()
        self.requests = Counter(
            "service_probe_requests_total",
            "Completed target probes",
            [*LABELS, "outcome"],
            registry=self.registry,
        )
        self.duration = Histogram(
            "service_probe_duration_seconds",
            "Target probe duration",
            LABELS,
            registry=self.registry,
        )

    @contextmanager
    def exclusive(self, identifier):
        lock = self.locks.setdefault(identifier, Lock())
        if not lock.acquire(blocking=False):
            raise HTTPException(409, "Probe or configuration update is in progress")
        try:
            yield
        finally:
            lock.release()

    def forget(self, identifier):
        self.token_sessions.pop(identifier, None)
        epoch = self.epochs.pop(identifier, None)
        self.due.pop(identifier, None)
        if epoch:
            for outcome in ("success", "failure"):
                self.requests.remove(*epoch, outcome)
            self.duration.remove(*epoch)

    async def run(self, identifier, authorize=None):
        with self.exclusive(identifier):
            async with self.semaphore:
                if authorize:
                    await asyncio.to_thread(authorize)
                with SessionLocal() as db:
                    row = repository.current(db, identifier)
                    if not row or row.deleted_at:
                        raise HTTPException(404, "Monitor not found")
                    application = db.get(Application, row.application_id)
                    if (
                        not row.enabled
                        or not application
                        or application.deleted_at
                        or not application.enabled
                    ):
                        raise HTTPException(409, "Monitoring is paused")
                    server = (
                        repository.server(db, row.probe_server_id) if row.probe_server_id else None
                    )
                    if row.probe_server_id and (not server or server.deleted_at):
                        raise HTTPException(409, "Probe source is retired")
                labels = (str(row.id), str(row.application_id), row.config_changed_at.isoformat())
                if self.epochs.get(identifier) != labels:
                    self.forget(identifier)
                    self.epochs[identifier] = labels
                    for outcome in ("success", "failure"):
                        self.requests.labels(*labels, outcome)
                observation = await probe(
                    row, server, self.token_sessions.setdefault(identifier, TokenSession())
                )
                if authorize:
                    await asyncio.to_thread(authorize)
                with SessionLocal() as db:
                    current = repository.current(db, identifier)
                    if (
                        not current
                        or current.deleted_at
                        or current.config_changed_at != row.config_changed_at
                        or not current.enabled
                    ):
                        return
                    current.last_probe_at = observation.checked_at
                    current.last_probe_success = observation.success
                    current.last_error_code = observation.error_code
                    current.last_http_status = observation.http_status
                    current.last_latency_seconds = observation.latency_seconds
                    current.last_evidence = observation.evidence
                    if observation.success is True:
                        current.last_success_at = observation.checked_at
                    elif observation.success is False:
                        current.last_failure_at = observation.checked_at
                    db.commit()
                if observation.success is not None:
                    self.requests.labels(
                        *labels, "success" if observation.success else "failure"
                    ).inc()
                    self.duration.labels(*labels).observe(observation.latency_seconds)
                self.due[identifier] = time.monotonic() + row.interval_seconds
                return observation

    async def scheduled(self, identifier):
        try:
            await self.run(identifier)
        except Exception as exc:
            logger.warning("Service probe failed (%s)", type(exc).__name__)
            self.due[identifier] = time.monotonic() + 15

    async def schedule_once(self):
        with SessionLocal() as db:
            rows = repository.active_monitors(db)
        active = {row.id for row in rows}
        for identifier in list(self.epochs):
            if identifier not in active:
                self.forget(identifier)
        for row in rows:
            if (
                row.id in self.epochs
                and self.epochs[row.id][2] != row.config_changed_at.isoformat()
            ):
                self.forget(row.id)
            now = time.monotonic()
            due = self.due.setdefault(row.id, now + random.uniform(0, min(5, row.interval_seconds)))
            if due > now or len(self.tasks) >= 4 or self.locks.get(row.id, Lock()).locked():
                continue
            task = asyncio.create_task(self.scheduled(row.id))
            self.tasks.add(task)
            task.add_done_callback(self.tasks.discard)

    async def scheduler(self):
        while True:
            try:
                await self.schedule_once()
            except Exception as exc:
                logger.warning("Service probe scheduler failed (%s)", type(exc).__name__)
            await asyncio.sleep(2)

    async def close(self):
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        self.token_sessions.clear()

    def metrics(self, db):
        rows = repository.active_monitors(db)
        registry = CollectorRegistry()

        class Snapshot:
            def collect(self):
                names = {
                    "service_probe_success": "Last target outcome",
                    "service_probe_last_run_timestamp_seconds": "Last completed probe",
                    "service_probe_interval_seconds": "Probe interval",
                    "service_probe_has_result": "Has target evidence",
                    "service_probe_latency_seconds": "Last target duration",
                }
                for name, help_text in names.items():
                    metric = GaugeMetricFamily(name, help_text, labels=LABELS)
                    for row in rows:
                        value = {
                            "service_probe_success": int(bool(row.last_probe_success)),
                            "service_probe_last_run_timestamp_seconds": (
                                row.last_probe_at or row.config_changed_at
                            ).timestamp(),
                            "service_probe_interval_seconds": row.interval_seconds,
                            "service_probe_has_result": int(row.last_probe_success is not None),
                            "service_probe_latency_seconds": row.last_latency_seconds or 0,
                        }[name]
                        metric.add_metric(
                            [
                                str(row.id),
                                str(row.application_id),
                                row.config_changed_at.isoformat(),
                            ],
                            value,
                        )
                    yield metric

        registry.register(Snapshot())
        return generate_latest(registry) + generate_latest(self.registry)
