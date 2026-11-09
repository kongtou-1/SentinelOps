import json
import math
import time
from datetime import UTC, datetime

from fastapi import HTTPException

from app.integrations.prometheus import prometheus
from app.models import Application, ServiceMonitor, ServiceObjective
from app.repositories import operations as repository
from app.services.pagination import page
from app.services.records import active_record


def listing(db, application_id, response, offset=0, limit=50):
    active_record(db, Application, application_id)
    return page(db, repository.objectives(application_id), response, offset, limit)


def configure(db, application_id, body, identifier=None, actor=None):
    application = active_record(db, Application, application_id)
    row = repository.objective_for_update(db, identifier) if identifier else None
    if identifier and (not row or row.application_id != application_id):
        raise HTTPException(404, "Objective not found")
    if body.expected_version != (row.version if row else 0):
        raise HTTPException(409, "Objective version changed")
    if body.monitor_id:
        monitor = active_record(db, ServiceMonitor, body.monitor_id)
        if monitor.application_id != application.id:
            raise HTTPException(422, "Monitor belongs to another service")
        if body.kind == "request_p95" and not monitor.route:
            raise HTTPException(422, "Configure the monitor's route template first")
    if not row:
        row = ServiceObjective(application_id=application_id, version=0)
        db.add(row)
    for key, value in body.model_dump(exclude={"expected_version"}).items():
        setattr(row, key, value)
    row.version += 1
    row.evaluation, row.checked_at = {}, None
    db.flush()
    if actor:
        repository.service_audit(
            db,
            application_id,
            actor,
            "service_objective_saved",
            {"objective_id": str(row.id), "version": row.version},
        )
    db.commit()
    db.refresh(row)
    return row


def remove(db, identifier, expected_version, actor=None):
    row = repository.objective_for_update(db, identifier)
    if not row:
        raise HTTPException(404, "Objective not found")
    if row.version != expected_version:
        raise HTTPException(409, "Objective version changed")
    row.deleted_at, row.enabled = datetime.now(UTC), False
    if actor:
        repository.service_audit(
            db,
            row.application_id,
            actor,
            "service_objective_deleted",
            {"objective_id": str(row.id), "version": row.version},
        )
    db.commit()


def scalar(expression, now):
    result = prometheus(
        "/api/v1/query", {"query": expression, "time": now.timestamp()}, timeout_seconds=3
    )
    values = result.get("result", [])
    if len(values) != 1:
        return None
    value = float(values[0]["value"][1])
    return value if math.isfinite(value) else None


def expressions(row, application, monitor):
    window = f"{row.window_days}d"
    app_labels = (
        f'job="service-traffic",application_id={json.dumps(str(application.id))},'
        f"source_epoch={json.dumps(application.traffic_config_changed_at.isoformat())}"
    )
    if getattr(row, "metric_source", "application") == "platform":
        app_labels = 'job="platform"'
    if row.kind == "probe_availability":
        labels = (
            f"monitor_id={json.dumps(str(monitor.id))},"
            f"source_epoch={json.dumps(monitor.config_changed_at.isoformat())}"
        )
        total = f"sum(increase(service_probe_requests_total{{{labels}}}[{window}]))"
        good = (
            f'sum(increase(service_probe_requests_total{{{labels},outcome="success"}}[{window}]))'
        )
        health = 'up{job="service-probe"}'
        return total, f"({good}) / ({total})", health
    health = f"up{{{app_labels}}}"
    business = f'{app_labels},traffic="business"'
    operation = f"operation={json.dumps(row.operation)}"
    if row.kind == "business_success":
        labels = f"{business},{operation}"
        total = f"sum(increase(sentinelops_business_requests_total{{{labels}}}[{window}]))"
        good_labels = labels + ',outcome="success"'
        good = f"sum(increase(sentinelops_business_requests_total{{{good_labels}}}[{window}]))"
        return total, f"({good}) / ({total})", health
    if row.kind == "request_p95":
        labels = f"{business},method={json.dumps(monitor.method)},route={json.dumps(monitor.route)}"
        metric = "sentinelops_http_request_duration_seconds"
    else:
        labels, metric = f"{business},{operation}", "sentinelops_llm_ttft_seconds"
    total = f"sum(increase({metric}_count{{{labels}}}[{window}]))"
    buckets = f"sum by(le)(increase({metric}_bucket{{{labels}}}[{window}]))"
    return total, f"histogram_quantile(0.95, {buckets})", health


def evaluate(row, application, monitor=None, now=None):
    now = now or datetime.now(UTC)
    base = {"value": None, "samples": None, "coverage": 0, "error_budget_remaining": None}

    def state(status, reason, **values):
        return {**base, "status": status, "reason": reason, **values}

    if not row.enabled or not application.enabled:
        return state("paused", "monitoring_paused")
    if row.kind in ("probe_availability", "request_p95") and (
        not monitor or monitor.deleted_at or not monitor.enabled
    ):
        return state("unconfigured", "monitor_unavailable")
    if (
        row.kind != "probe_availability"
        and not application.traffic_target
        and getattr(row, "metric_source", "application") != "platform"
    ):
        return state("unconfigured", "traffic_unconfigured")
    try:
        total_expression, value_expression, health = expressions(row, application, monitor)
        healthy = scalar(f"min({health})", now)
        sampled_at = scalar(f"min(timestamp({health}))", now)
        if healthy != 1 or sampled_at is None or not -5 <= now.timestamp() - sampled_at <= 60:
            return state("stale", "collector_missing_or_stale")
        step = 60 if row.window_days <= 7 else 300
        seconds = row.window_days * 86400
        history = prometheus(
            "/api/v1/query_range",
            {
                "query": f"min({health} and (time() - timestamp({health}) < 60))",
                "start": now.timestamp() - seconds,
                "end": now.timestamp(),
                "step": step,
            },
            timeout_seconds=3,
        ).get("result", [])
        samples = history[0].get("values", []) if len(history) == 1 else []
        valid = sum(1 for _, value in samples if value == "1" or value == 1)
        coverage = min(1, valid / (seconds // step + 1))
        total = scalar(total_expression, now)
        if total is None:
            return state("unconfigured", "metric_not_reported", coverage=coverage)
        if total <= 0:
            return state("no_requests", "no_business_samples", samples=0, coverage=coverage)
        value = scalar(value_expression, now)
        if value is None:
            return state("unknown", "value_unavailable", samples=total, coverage=coverage)
        ratio = row.kind in ("business_success", "probe_availability")
        budget = total * (1 - row.target) - total * (1 - value) if ratio else None
        values = dict(value=value, samples=total, coverage=coverage, error_budget_remaining=budget)
        if coverage < 0.95:
            return state("provisional", "insufficient_history", **values)
        passed = value >= row.target if ratio else value <= row.target
        return state("met" if passed else "breached", "evaluated", **values)
    except (HTTPException, ValueError, TypeError, KeyError, OverflowError):
        return state("unknown", "observation_unavailable")


def evaluate_due(db, now=None):
    now = now or datetime.now(UTC)
    started = time.monotonic()
    for row in repository.due_objectives(db, now):
        if time.monotonic() - started >= 15:
            break
        identifier, version = row.id, row.version
        application = db.get(Application, row.application_id)
        if not application or application.deleted_at:
            row.evaluation = {"status": "unconfigured", "reason": "service_retired"}
            row.checked_at = now
            db.commit()
            continue
        epoch = application.traffic_config_changed_at
        monitor = db.get(ServiceMonitor, row.monitor_id) if row.monitor_id else None
        result = evaluate(row, application, monitor, now)
        db.rollback()
        current = repository.objective_for_update(db, identifier)
        if current and current.version == version:
            db.refresh(application)
            if application.traffic_config_changed_at != epoch or application.deleted_at:
                db.rollback()
                continue
            current.evaluation, current.checked_at = result, now
            db.commit()
