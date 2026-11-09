import math
from datetime import UTC, datetime

import app.repositories.monitoring.service_monitors as repository
from app.models import Application
from app.services.monitoring.service_monitors import metric_expression


def binding(db, event, frozen):
    monitor = repository.current(db, event.service_monitor_id)
    application = db.get(Application, monitor.application_id) if monitor else None
    if not monitor or monitor.deleted_at or not application or application.deleted_at:
        return monitor, application, "resource_retired"
    if not monitor.enabled or not application.enabled:
        return monitor, application, "monitoring_disabled"
    if monitor.config_changed_at.isoformat() != frozen["source_epoch"]:
        return monitor, application, "collection_changed"
    if (
        frozen.get("traffic_epoch")
        and application.traffic_config_changed_at.isoformat() != frozen["traffic_epoch"]
    ):
        return monitor, application, "traffic_collection_changed"
    if monitor.probe_server_id:
        server = repository.server(db, monitor.probe_server_id)
        if not server or server.deleted_at or not server.monitoring_enabled:
            return monitor, application, "probe_source_disabled_or_retired"
    return monitor, application, None


def evidence(db, event, frozen, rule, now):
    from app.services.monitoring.config import rule_comparison
    from app.services.monitoring.recovery import query

    monitor, application, reason = binding(db, event, frozen)
    if reason:
        return "terminated", reason, None
    at = monitor.last_probe_at
    if (
        not at
        or at <= (event.ends_at or event.starts_at)
        or not -5 <= (now - at).total_seconds() <= 2 * monitor.interval_seconds
    ):
        return "pending", "probe_missing_or_stale", None
    if monitor.last_probe_success is not True:
        return "pending", "probe_not_healthy", None
    if not rule.metric.startswith("service_probe_"):
        import json

        labels = (
            'job="service-traffic",application_id='
            + json.dumps(str(application.id))
            + ",source_epoch="
            + json.dumps(application.traffic_config_changed_at.isoformat())
        )
        health = "up{" + labels + "}"
        samples = query(f"timestamp({health}) and ({health} == 1)")
        times = [float(s["value"][1]) for s in samples]
        if not times or any(
            not math.isfinite(t)
            or t <= event.ends_at.timestamp()
            or not -5 <= now.timestamp() - t <= 60
            for t in times
        ):
            return "pending", "traffic_collector_missing_or_stale", None
        at = min(at, datetime.fromtimestamp(min(times), UTC))
    values = query(metric_expression(rule.metric, monitor, application))
    if not values:
        return "pending", "normal_evidence_missing", None
    numbers = [float(s["value"][1]) for s in values]
    if not all(math.isfinite(v) for v in numbers):
        return "pending", "normal_evidence_unavailable", None
    failing = any(
        v < rule.threshold if rule_comparison(rule.metric) == "<" else v > rule.threshold
        for v in numbers
    )
    return (
        ("pending", "condition_still_failing", None)
        if failing
        else ("verified", "fresh_normal_service_evidence", at)
    )
