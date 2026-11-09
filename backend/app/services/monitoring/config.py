import json
import os
import re
from uuid import UUID

import yaml
from fastapi import HTTPException

import app.repositories.monitoring.config as _repository
from app.core.config import get_settings
from app.integrations.prometheus import prometheus
from app.services.monitoring.metrics import llm_metric_expression, metric_expression


def rule_comparison(metric):
    return (
        "<"
        if metric
        in (
            "target_up",
            "http_up",
            "container_up",
            "service_probe_up",
            "llm_up",
            "host_memory_available_bytes",
            "host_disk_available_bytes",
        )
        else ">"
    )


def rule_config(rule):
    result = {
        key: str(value) if isinstance(value, UUID) else value
        for key in (
            "id",
            "metric",
            "threshold",
            "for_seconds",
            "server_id",
            "llm_endpoint_id",
            "prediction_task_id",
            "prediction_horizon_hours",
        )
        for value in [getattr(rule, key)]
    }
    if getattr(rule, "service_monitor_id", None):
        result["service_monitor_id"] = str(rule.service_monitor_id)
    return result


def discovery_targets(db):
    groups = []
    for server in _repository.discovery_targets_query(db):
        for job, target in (
            ("node", server.node_exporter_target),
            ("cadvisor", server.cadvisor_target),
            ("docker-state", server.docker_state_target),
            ("blackbox", server.health_url),
        ):
            if target:
                groups.append(
                    {
                        "targets": [target],
                        "labels": {
                            "collector": job,
                            "server_id": str(server.id),
                            "server_name": server.name,
                            "environment": server.environment,
                            "source_epoch": server.collection_config_changed_at.isoformat(),
                        },
                    }
                )
    from app.repositories.monitoring.service_monitors import traffic_applications

    for application in traffic_applications(db):
        groups.append(
            {
                "targets": [application.traffic_target],
                "labels": {
                    "collector": "service-traffic",
                    "application_id": str(application.id),
                    "source_epoch": application.traffic_config_changed_at.isoformat(),
                },
            }
        )
    return groups


def apply_rules(db):
    # Serialize writers across API processes. Only this explicit Admin operation applies rules.

    _repository.apply_rules_query(db)
    rules = []
    for rule in _repository.apply_rules_query_2(db):
        monitor = application = None
        if rule.service_monitor_id:
            monitor, application = _repository.service_rule_query(db, rule)
            if (
                not monitor
                or monitor.deleted_at
                or not monitor.enabled
                or not application
                or application.deleted_at
                or not application.enabled
            ):
                continue
            if not rule.metric.startswith("service_probe_") and (
                not application.traffic_target or not monitor.route
            ):
                continue
        if rule.prediction_task_id:
            from app.services.predictions.tasks import source

            task = _repository.apply_rules_query_3(db, rule)
            if not task or task.deleted_at is not None or not task.enabled:
                continue
            try:
                _, enabled = source(db, task)
            except HTTPException:
                continue
            if not enabled:
                continue
        if rule.server_id:
            server = _repository.apply_rules_query_4(db, rule)
            if server.deleted_at is not None or not server.monitoring_enabled:
                continue
        if rule.llm_endpoint_id:
            endpoint = _repository.apply_rules_query_5(db, rule)
            if endpoint.deleted_at is not None or not endpoint.probe_enabled:
                continue
        comparison = rule_comparison(rule.metric)
        if monitor:
            from app.services.monitoring.service_monitors import (
                metric_expression as service_expression,
            )

            expression = service_expression(rule.metric, monitor, application)
        elif rule.prediction_task_id:
            selector = f'task_id="{rule.prediction_task_id}"'
            if rule.prediction_horizon_hours:
                selector += f',horizon_hours="{rule.prediction_horizon_hours}"'
            expression = rule.metric + "{" + selector + "}"
        else:
            expression = (
                llm_metric_expression(rule.metric, rule.llm_endpoint_id)
                if rule.metric.startswith("llm_")
                else metric_expression(rule.metric, rule.server_id)
            )
        labels = {"severity": rule.severity, "rule_id": str(rule.id)}
        if rule.metric in (
            "llm_authentication_failed",
            "llm_insufficient_balance",
            "llm_rate_limited",
            "llm_service_error",
            "llm_probe_stale",
        ):
            labels["error_type"] = rule.metric.removeprefix("llm_")
        annotations = {
            "platform_rule_snapshot": json.dumps(
                {**rule_config(rule), "source_epoch": "{{ $labels.source_epoch }}"},
                separators=(",", ":"),
            ),
            "summary": rule.code,
            "description": (
                "endpoint={{ $labels.endpoint_id }} error={{ $labels.error_type }} "
                "value={{ $value }}"
                if rule.metric.startswith("llm_")
                else "{{ $labels.instance }}: {{ $value }}"
            ),
        }
        if monitor:
            labels.update(monitor_id=str(monitor.id), application_id=str(application.id))
            frozen = {**rule_config(rule), "source_epoch": monitor.config_changed_at.isoformat()}
            if not rule.metric.startswith("service_probe_"):
                frozen["traffic_epoch"] = application.traffic_config_changed_at.isoformat()
            annotations["platform_rule_snapshot"] = json.dumps(frozen, separators=(",", ":"))
            annotations["description"] = "monitor={{ $labels.monitor_id }} value={{ $value }}"
        if rule.prediction_task_id:
            from app.services.predictions.tasks import snapshot

            frozen_rule = {
                **rule_config(rule),
                "source_epoch": "{{ $labels.source_epoch }}",
                "prediction_config": snapshot(db, task),
            }
            annotations["platform_rule_snapshot"] = json.dumps(frozen_rule, separators=(",", ":"))
            horizon = rule.prediction_horizon_hours or 1
            match = f'task_id="{task.id}",horizon_hours="{horizon}"'
            info_query = json.dumps("prediction_model_info{" + match + "}")
            breach_query = json.dumps("prediction_first_breach_timestamp_seconds{" + match + "}")
            annotations.update(
                {
                    "metric": task.metric,
                    "description": (
                        "task={{ $labels.task_id }} horizon={{ $labels.horizon_hours }} "
                        "value={{ $value }}"
                    ),
                    "first_breach": "{{ with query "
                    + breach_query
                    + " }}{{ . | first | value }}{{ end }}",
                    "model": "{{ with query "
                    + info_query
                    + " }}{{ (index . 0).Labels.model }}{{ end }}",
                    "model_quality": "{{ with query "
                    + info_query
                    + " }}{{ (index . 0).Labels.quality }}{{ end }}",
                }
            )
        from app.services.operations.impact import recovery_seconds

        frozen_rule = json.loads(annotations["platform_rule_snapshot"])
        frozen_rule["stability_seconds"] = recovery_seconds(db, rule)
        annotations["platform_rule_snapshot"] = json.dumps(frozen_rule, separators=(",", ":"))
        rules.append(
            {
                "alert": rule.code,
                "expr": f"({expression}) {comparison} {rule.threshold:g}",
                "for": f"{rule.for_seconds}s",
                "labels": labels,
                "annotations": annotations,
            }
        )
    directory = get_settings().monitoring_rules_dir
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "platform.yml"
    previous = path.read_bytes() if path.exists() else None
    temporary = directory / "platform.tmp"
    try:
        temporary.write_text(
            yaml.safe_dump({"groups": [{"name": "platform", "rules": rules}]}), encoding="utf-8"
        )
        temporary.chmod(0o644)
        os.replace(temporary, path)
        prometheus("/-/reload", method="POST")
        verify_active_rules(rules)
    except (OSError, HTTPException):
        try:
            if previous is not None:
                temporary.write_bytes(previous)
                temporary.chmod(0o644)
                os.replace(temporary, path)
            else:
                path.unlink(missing_ok=True)
            restored = yaml.safe_load(previous) if previous else {"groups": []}
            old_rules = [
                r for g in restored["groups"] if g["name"] == "platform" for r in g["rules"]
            ]
            # A timed-out response does not prove the reload failed. Restore runtime too.
            prometheus("/-/reload", method="POST")
            verify_active_rules(old_rules)
        except (OSError, HTTPException, ValueError, KeyError, yaml.YAMLError):
            raise HTTPException(502, "Rule apply failed; effective rule state is unknown") from None
        raise HTTPException(
            502, "Rule apply failed; previous file and active rules restored"
        ) from None
    db.commit()
    return {"applied": len(rules)}


def verify_active_rules(expected):
    data = prometheus("/api/v1/rules", {"type": "alert"})
    actual = [r for g in data["groups"] if g["name"] == "platform" for r in g["rules"]]

    def canonical_expression(expression):
        # Prometheus sorts label matchers when serializing an expression.
        def selector(match):
            labels = re.findall(r'[a-zA-Z_]\w*(?:=~|!~|!=|=)"(?:\\.|[^"\\])*"', match[1])
            return "{" + ",".join(sorted(labels)) + "}"

        expression = re.sub(r"\{([^{}]*)\}", selector, expression)
        # The fixed metric catalog has no numeric literals in quoted labels;
        # legacy files emitted thresholds such as 85.0, serialized as 85 by Prometheus.
        expression = re.sub(r"\b(\d+)\.0+\b", r"\1", expression)
        return re.sub(r"\s+", "", expression)

    def signature(rule, active=False):
        return (
            rule["name" if active else "alert"],
            canonical_expression(rule["query" if active else "expr"]),
            float(rule["duration"]) if active else float(rule["for"][:-1]),
            rule["labels"],
            rule["annotations"],
        )

    if sorted((signature(r, True) for r in actual), key=lambda r: r[0]) != sorted(
        (signature(r) for r in expected), key=lambda r: r[0]
    ):
        raise HTTPException(502, "Active Prometheus rules do not match the requested rules")
