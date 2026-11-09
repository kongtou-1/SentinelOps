"""Durable queue and Prometheus access. Contains no numeric runtime imports."""

import json
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException
from prometheus_client import CollectorRegistry, Gauge, generate_latest

import app.repositories.predictions.tasks as _repository
from app.integrations.prometheus import prometheus
from app.models import Container, LLMEndpoint, PredictionResult, Server
from app.services.monitoring.metrics import llm_metric_expression, metric_expression
from app.services.records import active_record, save

ACTIVE = ("queued", "running")


def source(db, task):
    if task.llm_endpoint_id:
        record = active_record(db, LLMEndpoint, task.llm_endpoint_id)
        return record, record.probe_enabled
    if task.container_id:
        container = active_record(db, Container, task.container_id)
        server = active_record(db, Server, container.server_id)
        return server, server.monitoring_enabled
    record = active_record(db, Server, task.server_id)
    return record, record.monitoring_enabled


def snapshot(db, task):
    record, _ = source(db, task)
    config = {
        key: getattr(task, key)
        for key in ("name", "metric", "selector", "threshold", "interval_seconds")
    }
    for key in ("server_id", "container_id", "llm_endpoint_id"):
        value = getattr(task, key)
        config[key] = str(value) if value else None
    if task.container_id:
        config["server_id"] = str(record.id)
        config["docker_id"] = _repository.snapshot_query(db, task).docker_id
    config["source_epoch"] = (
        record.probe_config_changed_at.isoformat()
        if task.llm_endpoint_id
        else record.collection_config_changed_at.isoformat()
    )
    if task.llm_endpoint_id:
        config["probe_interval_seconds"] = record.probe_interval_seconds
    return config


def assert_idle(db, task_id):
    if _repository.assert_idle_query(ACTIVE, db, task_id):
        raise HTTPException(409, "Prediction task is already queued or running")


def enqueue(db, task, trigger="manual", now=None):
    # Task row locking plus a partial unique index protects API/scheduler races.
    _repository.enqueue_query(db, task)
    if task.deleted_at:
        raise HTTPException(404, "Prediction task not found")
    assert_idle(db, task.id)
    source(db, task)
    now = now or datetime.now(UTC)
    result = PredictionResult(task_id=task.id, config=snapshot(db, task), trigger=trigger)
    task.next_run_at = now + timedelta(seconds=task.interval_seconds)
    return save(db, result)


def latest(db, task_id, completed=False):
    query = _repository.latest_query(task_id)
    if completed:
        query = _repository.latest_query_2(query)
    return _repository.latest_query_3(db, query)


def is_fresh(db, result, task, now=None):
    now = now or datetime.now(UTC)
    if result.status != "completed" or not result.data_end_at or not result.finished_at:
        return False
    max_age = timedelta(seconds=2 * task.interval_seconds)
    if now - result.data_end_at > max_age or now - result.finished_at > max_age:
        return False
    try:
        _, enabled = source(db, task)
        return enabled and result.config == snapshot(db, task)
    except HTTPException:
        return False


def expression(config):
    if config["llm_endpoint_id"]:
        if config["metric"] == "llm_error_rate":
            value = (
                "1 - (" + llm_metric_expression("llm_success_rate", config["llm_endpoint_id"]) + ")"
            )
        else:
            value = llm_metric_expression(config["metric"], config["llm_endpoint_id"])
        endpoint = config["llm_endpoint_id"]
        value = (
            f"({value}) and on(endpoint_id) "
            f'(time() - llm_probe_last_run_timestamp_seconds{{endpoint_id="{endpoint}"}} <= 300)'
        )
    else:
        value = metric_expression(config["metric"], config["server_id"], config.get("docker_id"))
        job = "cadvisor" if config.get("container_id") else "node"
        value = (
            f"({value}) and on(server_id, source_epoch) "
            f'(up{{job="{job}",server_id="{config["server_id"]}",'
            f"source_epoch={json.dumps(config['source_epoch'])}}} == 1)"
        )
    # Existing expressions compute rates before bin aggregation. Missing bins stay absent.
    window = f"({value})[5m:15s]"
    return f"avg_over_time({window}) and (count_over_time({window}) >= 19)"


def load_history(config, now=None):
    now = now or datetime.now(UTC)
    end = int(now.timestamp()) // 300 * 300
    if config.get("probe_interval_seconds", 300) > 300:
        return [], end
    start = max(
        end - 28 * 86400 + 300,
        int(datetime.fromisoformat(config["source_epoch"]).timestamp()) // 300 * 300 + 300,
    )
    if start > end:
        return [], end
    data = prometheus(
        "/api/v1/query_range",
        {
            "query": expression(config),
            "start": start,
            "end": end,
            "step": 300,
        },
    )
    matching = [
        series
        for series in data.get("result", [])
        if all(series.get("metric", {}).get(k) == v for k, v in config["selector"].items())
    ]
    if len(matching) > 1:
        raise HTTPException(422, "Ambiguous prediction series")
    return (matching[0].get("values", []) if matching else []), end


def schedule_due(db, now=None):
    now = now or datetime.now(UTC)
    ids = _repository.schedule_due_query(db, now)
    count = 0
    for task_id in ids:
        task = _repository.schedule_due_query_2(db, task_id)
        if not task:
            continue
        try:
            _, enabled = source(db, task)
            if enabled and task.enabled and not task.deleted_at and task.next_run_at <= now:
                enqueue(db, task, "scheduled", now)
                count += 1
            else:
                db.rollback()
        except HTTPException:
            db.rollback()
    return count


def claim(db):
    result = _repository.claim_query(db)
    if result:
        result.status = "running"
        result.started_at = datetime.now(UTC)
        db.commit()
    return result


def interrupt_runs(db):
    for run in _repository.interrupt_runs_query(db):
        run.status = "interrupted"
        run.error_code = "worker_restarted"
        run.finished_at = datetime.now(UTC)
    db.commit()


def complete(db, result_id, answer=None, error_code=None):
    result = _repository.complete_query(db, result_id)
    if result.status not in ACTIVE:
        return result
    task = _repository.complete_query_2(db, result)
    try:
        _, enabled = source(db, task)
        if task.deleted_at or not enabled or snapshot(db, task) != result.config:
            error_code = "resource_changed_or_disabled"
    except HTTPException:
        error_code = "resource_deleted"
    result.finished_at = datetime.now(UTC)
    result.status = "failed" if error_code else "completed"
    result.error_code = error_code
    if answer is not None and not error_code:
        result.data_end_at = datetime.fromtimestamp(answer.pop("data_end_at"), UTC)
        for key, value in answer.items():
            setattr(result, key, value)
    db.commit()
    return result


def export_metrics(db):
    registry = CollectorRegistry()
    labels = ["task_id", "server_id", "container_id", "endpoint_id", "source_epoch"]
    risk_metric = Gauge(
        "prediction_risk",
        "0 normal, 1 possible, 2 expected; absent is unknown",
        labels + ["horizon_hours"],
        registry=registry,
    )
    anomaly_metric = Gauge("prediction_anomaly", "Latest bin anomaly", labels, registry=registry)
    last_metric = Gauge(
        "prediction_last_run_timestamp_seconds",
        "Latest completed forecast",
        labels,
        registry=registry,
    )
    known = Gauge(
        "prediction_data_usable", "Fresh usable forecast available", labels, registry=registry
    )
    breach = Gauge(
        "prediction_first_breach_timestamp_seconds",
        "First threshold crossing, 0 none",
        ["task_id", "horizon_hours"],
        registry=registry,
    )
    info = Gauge(
        "prediction_model_info",
        "Selected model and quality",
        ["task_id", "horizon_hours", "model", "quality"],
        registry=registry,
    )
    for task in _repository.export_metrics_query(db):
        try:
            _, enabled = source(db, task)
            if not enabled:
                continue
        except HTTPException:
            continue
        result = latest(db, task.id)
        if result and result.status in ACTIVE:
            result = latest(db, task.id, completed=True)
        config = snapshot(db, task)
        values = [
            str(task.id),
            config["server_id"] or "",
            config["container_id"] or "",
            config["llm_endpoint_id"] or "",
            config["source_epoch"],
        ]
        usable = bool(
            result and is_fresh(db, result, task) and result.quality.get("status") == "usable"
        )
        known.labels(*values).set(int(usable))
        if not usable:
            continue
        last_metric.labels(*values).set(result.finished_at.timestamp())
        any_valid = False
        for hours, horizon in result.horizons.items():
            if horizon["quality"] not in ("validated", "interval_low_confidence"):
                continue
            level = horizon["risk"]["level"]
            if level == "unknown":
                continue
            any_valid = True
            risk_metric.labels(*values, hours).set(
                {"normal": 0, "possible": 1, "expected": 2}[level]
            )
            breach.labels(str(task.id), hours).set(horizon["risk"]["first_breach_at"] or 0)
            info.labels(str(task.id), hours, horizon["model"], horizon["quality"]).set(1)
        if any_valid:
            anomaly_metric.labels(*values).set(
                int(
                    any(
                        a["timestamp"] == int(result.data_end_at.timestamp())
                        for a in result.anomalies
                    )
                )
            )
    return generate_latest(registry)
