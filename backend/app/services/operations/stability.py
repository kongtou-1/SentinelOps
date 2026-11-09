"""Recovery stability uses distinct observations and a bounded historical check."""

import json
import math

from app.integrations.prometheus import prometheus
from app.models import Application, ServiceMonitor
from app.services.monitoring.config import rule_comparison
from app.services.monitoring.metrics import llm_metric_expression, metric_expression


def window_normal(db, event, rule, at, seconds):
    if event.service_monitor_id:
        from app.services.monitoring.service_monitors import metric_expression as service_expression

        monitor = db.get(ServiceMonitor, event.service_monitor_id)
        application = db.get(Application, monitor.application_id)
        expression = service_expression(rule.metric, monitor, application)
    elif event.prediction_task_id:
        labels = f'task_id="{event.prediction_task_id}"'
        if rule.prediction_horizon_hours:
            labels += f',horizon_hours="{rule.prediction_horizon_hours}"'
        expression = f"{rule.metric}{{{labels}}}"
    elif event.llm_endpoint_id:
        expression = llm_metric_expression(rule.metric, event.llm_endpoint_id)
        labels = (
            f'endpoint_id="{event.llm_endpoint_id}",'
            f"source_epoch={json.dumps(event.rule_snapshot['source_epoch'])}"
        )
        fresh = (
            f"(time() - llm_probe_last_run_timestamp_seconds{{{labels}}}) < "
            f"(2 * llm_probe_interval_seconds{{{labels}}})"
        )
        expression = (
            f"({expression}) and on(endpoint_id) ({fresh}) and on(endpoint_id) "
            f"(llm_probe_success{{{labels}}} == 1)"
        )
    else:
        expression = metric_expression(rule.metric, event.server_id)
        job = (
            "blackbox"
            if rule.metric.startswith("http_")
            else "docker-state"
            if rule.metric in ("container_restarts_5m", "container_oom_5m", "container_unhealthy")
            else "inventory"
            if rule.metric == "container_up"
            else "cadvisor"
            if rule.metric.startswith("container_")
            else "node"
        )
        labels = (
            f'server_id="{event.server_id}",'
            f"source_epoch={json.dumps(event.rule_snapshot['source_epoch'])}"
        )
        health = (
            f'up{{job="{job}",{labels}}}'
            if job != "inventory"
            else f"platform_inventory_up{{{labels}}}"
        )
        guard = f"({health} == 1) and (time() - timestamp({health}) < 60)"
        expression = f"({expression}) and on(server_id) ({guard})"
    result = prometheus(
        "/api/v1/query_range",
        {
            "query": expression,
            "start": at.timestamp() - seconds,
            "end": at.timestamp(),
            "step": 1,
            "timeout": "2s",
        },
        timeout_seconds=2,
    ).get("result", [])
    identity = {
        k: v
        for k, v in event.labels.items()
        if k
        in (
            "server_id",
            "endpoint_id",
            "monitor_id",
            "id",
            "docker_id",
            "container_id",
            "mountpoint",
            "device",
            "source_epoch",
        )
    }
    matching = [
        s
        for s in result
        if all(
            s.get("metric", {}).get(k) == v
            for k, v in identity.items()
            if k != "source_epoch" or k in s.get("metric", {})
        )
    ]
    expected = seconds + 1
    if not matching:
        return False
    for series in matching:
        values = series.get("values", [])
        if len({float(t) for t, _ in values}) < expected:
            return False
        numbers = [float(v) for _, v in values]
        if not all(math.isfinite(v) for v in numbers):
            return False
        if any(
            v < rule.threshold if rule_comparison(rule.metric) == "<" else v > rule.threshold
            for v in numbers
        ):
            return False
    return True


def advance(event, outcome, reason, at, history_normal=None):
    seconds = event.rule_snapshot.get("stability_seconds", 0)
    if not seconds:
        return outcome, reason, at
    if outcome != "verified" or not at:
        event.stability_started_at = event.stability_last_evidence_at = None
        return outcome, reason, at
    previous = event.stability_last_evidence_at
    if previous and at <= previous:
        return "pending", "stability_waiting_new_sample", None
    if previous and (at - previous).total_seconds() > max(120, seconds):
        event.stability_started_at = None
    event.stability_last_evidence_at = at
    if not event.stability_started_at:
        event.stability_started_at = at
    if (at - event.stability_started_at).total_seconds() < seconds:
        return "pending", "stability_window_pending", None
    if not history_normal:
        event.stability_started_at = at
        return "pending", "stability_history_incomplete_or_failing", None
    return "verified", "stable_normal_evidence", at
