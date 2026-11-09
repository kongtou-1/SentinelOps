"""Resolved notifications require fresh, rule-bound evidence before incident recovery."""

import json
import math
import re
import time
from datetime import UTC, datetime
from uuid import UUID

from fastapi import HTTPException

import app.repositories.monitoring.recovery as _repository
from app.integrations.prometheus import prometheus
from app.models import (
    AlertRule,
    LLMEndpoint,
    PredictionTask,
    Server,
)
from app.services.incidents.logic import append_timeline, event_incident, lock_incidents, recompute
from app.services.monitoring.config import rule_comparison, rule_config
from app.services.monitoring.metrics import llm_metric_expression, metric_expression


def notification_snapshot(annotations):
    try:
        value = json.loads(annotations.get("platform_rule_snapshot", "{}"))
        if not isinstance(value, dict) or set(value) - {
            "prediction_config",
            "service_monitor_id",
            "traffic_epoch",
            "stability_seconds",
        } != {
            "id",
            "metric",
            "threshold",
            "for_seconds",
            "server_id",
            "llm_endpoint_id",
            "prediction_task_id",
            "prediction_horizon_hours",
            "source_epoch",
        }:
            return {}
        UUID(value["id"])
        if value.get("service_monitor_id"):
            UUID(value["service_monitor_id"])
        if not value["source_epoch"] or not math.isfinite(float(value["threshold"])):
            return {}
        if "stability_seconds" in value and (
            type(value["stability_seconds"]) is not int
            or not 30 <= value["stability_seconds"] <= 1800
        ):
            return {}
        return value
    except (ValueError, TypeError, KeyError):
        return {}


def query(expression):
    return prometheus(
        "/api/v1/query", {"query": expression, "timeout": "2s"}, timeout_seconds=2
    ).get("result", [])


def evidence(db, event, now):
    frozen = event.rule_snapshot
    if not frozen:
        # Legacy notifications still retain trustworthy asset/rule references.
        # Explicit retirement/disablement is a termination, never recovery proof.
        for model, identifier in (
            (AlertRule, event.rule_id),
            (Server, event.server_id),
            (LLMEndpoint, event.llm_endpoint_id),
            (PredictionTask, event.prediction_task_id),
        ):
            if identifier:
                record = _repository.evidence_query(db, identifier, model)
                if not record or record.deleted_at:
                    return "terminated", "resource_or_rule_retired", None
                enabled = (
                    record.monitoring_enabled
                    if model == Server
                    else record.probe_enabled
                    if model == LLMEndpoint
                    else record.enabled
                )
                if not enabled:
                    return "terminated", "resource_or_rule_disabled", None
        return "pending", "rule_snapshot_unavailable", None
    rule = _repository.evidence_query_2(db, event)
    if not rule or rule.deleted_at or not rule.enabled:
        return "terminated", "rule_disabled_or_deleted", None
    if rule_config(rule) != {
        k: v
        for k, v in frozen.items()
        if k not in ("source_epoch", "prediction_config", "traffic_epoch", "stability_seconds")
    }:
        return "terminated", "rule_changed", None
    if event.service_monitor_id:
        from app.services.monitoring.service_recovery import evidence as service_evidence

        return service_evidence(db, event, frozen, rule, now)
    task = _repository.evidence_query_3(db, event)
    if event.prediction_task_id:
        if not task or task.deleted_at or not task.enabled:
            return "terminated", "prediction_disabled_or_deleted", None
        from app.services.predictions.tasks import is_fresh, latest, snapshot, source

        try:
            record, enabled = source(db, task)
            current = snapshot(db, task)
            epoch = current["source_epoch"]
        except HTTPException:
            return "terminated", "resource_retired", None
        if not enabled:
            return "terminated", "monitoring_disabled", None
        if epoch != frozen["source_epoch"]:
            return "terminated", "collection_changed", None
        if frozen.get("prediction_config") != current:
            return "terminated", "prediction_configuration_changed", None
        result = latest(db, task.id, completed=True)
        if (
            not result
            or not is_fresh(db, result, task, now)
            or not result.data_end_at
            or result.data_end_at <= (event.ends_at or event.starts_at)
        ):
            return "pending", "prediction_unknown_or_stale", None
        if rule.metric == "prediction_anomaly":
            if result.quality.get("status") != "usable" or not any(
                h.get("quality") in ("validated", "interval_low_confidence")
                for h in result.horizons.values()
            ):
                return "pending", "prediction_unknown_or_stale", None
            value = int(
                any(a["timestamp"] == int(result.data_end_at.timestamp()) for a in result.anomalies)
            )
        else:
            level = (
                result.horizons.get(str(rule.prediction_horizon_hours), {})
                .get("risk", {})
                .get("level", "unknown")
            )
            if level == "unknown":
                return "pending", "prediction_unknown_or_stale", None
            value = {"normal": 0, "possible": 1, "expected": 2}[level]
        return (
            ("pending", "condition_still_failing", None)
            if value > rule.threshold
            else ("verified", "fresh_normal_prediction", result.data_end_at)
        )
    endpoint, server = _repository.evidence_query_4(db, event)
    resource = endpoint or server
    if not resource or resource.deleted_at:
        return "terminated", "resource_retired", None
    enabled = endpoint.probe_enabled if endpoint else server.monitoring_enabled
    epoch = endpoint.probe_config_changed_at if endpoint else server.collection_config_changed_at
    if not enabled:
        return "terminated", "monitoring_disabled", None
    if epoch.isoformat() != frozen["source_epoch"]:
        return "terminated", "collection_changed", None
    if event.labels.get("container_id"):
        try:
            container = _repository.evidence_query_5(db, event)
        except ValueError:
            container = None
        if not container or container.deleted_at:
            return "terminated", "resource_retired", None
    elif event.labels.get("id"):
        identity = event.labels["id"]
        match = re.search(r"([a-f0-9]{64})(?:\.scope)?$", identity)
        containers = _repository.evidence_query_6(db, event)
        matching_containers = [
            c
            for c in containers
            if c.docker_id == identity or (match and match.group(1) in c.docker_id)
        ]
        if any(c.deleted_at for c in matching_containers):
            return "terminated", "resource_retired", None
    if endpoint:
        at = endpoint.last_probe_at
        if (
            not at
            or at <= (event.ends_at or event.starts_at)
            or (now - at).total_seconds() > 2 * endpoint.probe_interval_seconds
        ):
            return "pending", "probe_missing_or_stale", None
        expression = llm_metric_expression(rule.metric, endpoint.id)
        # A fresh successful probe is required even for stale/error/latency rule resolution.
        if not endpoint.last_probe_success:
            return "pending", "probe_not_healthy", None
    else:
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
        source_labels = f'server_id="{server.id}",source_epoch={json.dumps(frozen["source_epoch"])}'
        health = (
            f'up{{job="{job}"}}' if job == "inventory" else f'up{{job="{job}",{source_labels}}}'
        )
        if rule.metric == "target_up":
            health = f'up{{job=~"node|cadvisor",{source_labels}}}'
            if event.labels.get("job") in ("node", "cadvisor"):
                health = f'up{{job="{event.labels["job"]}",{source_labels}}}'
        # timestamp() must read the raw scrape, not an expression's evaluation time.
        samples = query(f"timestamp({health}) and ({health} == 1)")
        if not samples:
            return "pending", "collector_unavailable", None
        times = [float(s["value"][1]) for s in samples]
        if any(
            t <= (event.ends_at or event.starts_at).timestamp()
            or now.timestamp() - t > 60
            or t > now.timestamp() + 5
            for t in times
        ):
            return "pending", "collector_missing_or_stale", None
        at = datetime.fromtimestamp(min(times), UTC)
        if rule.metric == "container_unhealthy":
            polls = query(
                f'docker_state_last_poll_timestamp_seconds{{job="docker-state",{source_labels}}}'
            )
            poll_times = [float(s["value"][1]) for s in polls]
            if not poll_times or any(
                not math.isfinite(t)
                or t <= (event.ends_at or event.starts_at).timestamp()
                or not -5 <= now.timestamp() - t < 30
                for t in poll_times
            ):
                return "pending", "docker_state_missing_or_stale", None
            at = datetime.fromtimestamp(min(*times, *poll_times), UTC)
        expression = metric_expression(rule.metric, server.id)
        if rule.metric == "http_latency_seconds":
            expression = (
                f'({expression}) and (probe_success{{job="blackbox",server_id="{server.id}"}} == 1)'
            )
        if job == "inventory":
            healthy_inventory = query(
                f'platform_inventory_up{{server_id="{server.id}",source_epoch={json.dumps(frozen["source_epoch"])}}}'
            )
            if not healthy_inventory or any(float(s["value"][1]) != 1 for s in healthy_inventory):
                return "pending", "inventory_unavailable", None
    values = query(expression)
    identity = {
        k: v
        for k, v in event.labels.items()
        if k
        in (
            "server_id",
            "endpoint_id",
            "id",
            "docker_id",
            "container_id",
            "device",
            "mountpoint",
            "instance",
            "job",
            "source_epoch",
        )
    }
    matching = [
        s for s in values if all(s.get("metric", {}).get(k) == v for k, v in identity.items())
    ]
    if not matching:
        return "pending", "normal_evidence_missing", None
    numbers = [float(s["value"][1]) for s in matching]
    if not all(math.isfinite(v) for v in numbers):
        return "pending", "normal_evidence_unavailable", None
    failing = any(
        v < rule.threshold if rule_comparison(rule.metric) == "<" else v > rule.threshold
        for v in numbers
    )
    return (
        ("pending", "condition_still_failing", None)
        if failing
        else ("verified", "fresh_normal_evidence", at)
    )


def reconcile(db, limit=10, budget_seconds=15):
    started, now = time.monotonic(), datetime.now(UTC)
    oldest, recent = _repository.reconcile_query(db, limit)
    ids = list(dict.fromkeys([*recent, *oldest]))[:limit]
    db.rollback()
    changed = 0
    for identifier in ids:
        if time.monotonic() - started >= budget_seconds:
            break
        # Read evidence outside the incident lock; recheck the immutable binding inside it.
        event = _repository.reconcile_query_2(db, identifier)
        old_snapshot = dict(event.rule_snapshot)
        try:
            now = datetime.now(UTC)
            outcome, reason, at = evidence(db, event, now)
            history_normal = None
            seconds = old_snapshot.get("stability_seconds", 0)
            if (
                outcome == "verified"
                and seconds
                and event.stability_started_at
                and at
                and (at - event.stability_started_at).total_seconds() >= seconds
            ):
                from app.services.operations.stability import window_normal

                history_normal = window_normal(
                    db, event, _repository.evidence_query_2(db, event), at, seconds
                )
        except (HTTPException, ValueError, TypeError, OverflowError):
            outcome, reason, at = "pending", "evidence_source_unavailable", None
        db.rollback()
        lock_incidents(db)
        event = _repository.reconcile_query_3(db, identifier)
        incident = event_incident(db, event)
        if (
            not incident
            or incident.status not in ("open", "in_progress")
            or event.status != "resolved"
            or event.recovery_status != "pending"
            or event.rule_snapshot != old_snapshot
        ):
            db.rollback()
            continue
        # A concurrent asset/rule update must not turn old evidence into a success.
        if outcome == "verified":
            rule = _repository.reconcile_query_4(db, event)
            if (
                not rule
                or rule.deleted_at
                or not rule.enabled
                or rule_config(rule)
                != {
                    k: v
                    for k, v in old_snapshot.items()
                    if k
                    not in (
                        "source_epoch",
                        "prediction_config",
                        "traffic_epoch",
                        "stability_seconds",
                    )
                }
            ):
                outcome, reason, at = "terminated", "rule_changed", None
            # Asset configuration may change during the Prometheus request.
            endpoint, server = _repository.reconcile_query_5(db, event)
            resource = endpoint or server
            enabled = (
                endpoint.probe_enabled
                if endpoint
                else server.monitoring_enabled
                if server
                else False
            )
            epoch = (
                endpoint.probe_config_changed_at
                if endpoint
                else server.collection_config_changed_at
                if server
                else None
            )
            if event.service_monitor_id:
                from app.services.monitoring.service_recovery import binding

                resource, _, binding_error = binding(db, event, old_snapshot)
                enabled = binding_error is None
                epoch = resource.config_changed_at if resource else None
            if (
                not resource
                or resource.deleted_at
                or not enabled
                or not epoch
                or epoch.isoformat() != old_snapshot["source_epoch"]
            ):
                outcome, reason, at = "terminated", "resource_changed_or_retired", None
            if event.prediction_task_id:
                task = _repository.reconcile_query_6(db, event)
                from app.services.predictions.tasks import latest, snapshot

                result = latest(db, task.id) if task else None
                if (
                    not task
                    or task.deleted_at
                    or not task.enabled
                    or not result
                    or result.status != "completed"
                    or result.config != snapshot(db, task)
                ):
                    outcome, reason, at = "pending", "prediction_unknown_or_stale", None
        from app.services.operations.stability import advance

        outcome, reason, at = advance(event, outcome, reason, at, history_normal)
        before = (event.recovery_status, event.recovery_reason)
        (
            event.recovery_status,
            event.recovery_reason,
            event.recovery_checked_at,
            event.recovery_evidence_at,
        ) = outcome, reason, now, at
        if before != (outcome, reason):
            append_timeline(
                db,
                incident,
                "alert_recovery_checked",
                {
                    "alert_event_id": str(event.id),
                    "status": outcome,
                    "reason": reason,
                    "evidence_at": at.isoformat() if at else None,
                },
            )
            changed += 1
        recompute(db, incident)
        db.commit()
    return changed
