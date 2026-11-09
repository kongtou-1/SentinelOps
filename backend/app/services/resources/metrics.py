"""Read host and container metrics without importing resource mutations."""

from fastapi import HTTPException

from app.integrations.prometheus import prometheus
from app.models import Container, Server
from app.services.monitoring.metrics import metric_availability, metric_expression
from app.services.records import active_record


def metrics_result(metric, server_id, docker_id, start, end, step):
    if (start is None) != (end is None):
        raise HTTPException(422, "Provide both start and end for a range query")
    expression = metric_expression(metric, server_id, docker_id)
    # Rate functions can keep producing values after a failed scrape. Break those
    # historical curves as soon as their collector reports failure, too.
    if metric.startswith("host_") or metric in (
        "container_cpu_percent",
        "container_cpu_host_percent",
        "container_cpu_quota_percent",
        "container_memory_limit_bytes",
        "container_memory_bytes",
        "container_network_receive_bytes",
        "container_network_transmit_bytes",
    ):
        job = "node" if metric.startswith("host_") else "cadvisor"
        labels = f'job="{job}"' + (f',server_id="{server_id}"' if server_id else "")
        expression = f"({expression}) and on(server_id, source_epoch) (up{{{labels}}} == 1)"
    if start is None:
        return metric_availability(
            prometheus("/api/v1/query", {"query": expression, "timeout": "8s"})
        )
    seconds = (end - start).total_seconds()
    if seconds <= 0 or seconds > 31 * 86400 or seconds / step > 11000:
        raise HTTPException(422, "Invalid range: maximum 31 days and 11000 points per series")
    return metric_availability(
        prometheus(
            "/api/v1/query_range",
            {
                "query": expression,
                "start": start.timestamp(),
                "end": end.timestamp(),
                "step": step,
                "timeout": "8s",
            },
        ),
        start,
        end,
        step,
    )


def unconfigured_metric(server, metric, start):
    reason = (
        "monitoring_disabled"
        if not server.monitoring_enabled
        else "docker_state_not_configured"
        if metric in ("container_restarts_5m", "container_oom_5m", "container_unhealthy")
        and not server.docker_state_target
        else "http_probe_not_configured"
        if metric.startswith("http_") and not server.health_url
        else None
    )
    return (
        {
            "resultType": "matrix" if start else "vector",
            "result": [],
            "availability": "paused" if reason == "monitoring_disabled" else "unconfigured",
            "reason": reason,
        }
        if reason
        else None
    )


def server_metrics(server_id, metric, db, user, start=None, end=None, step=30):
    server = active_record(db, Server, server_id)
    return unconfigured_metric(server, metric, start) or metrics_result(
        metric, server_id, None, start, end, step
    )


def container_metrics(container_id, metric, db, user, start=None, end=None, step=30):
    container = active_record(db, Container, container_id)
    from app.services.access_control import container_server

    server = container_server(db, container)
    if not metric.startswith("container_"):
        raise HTTPException(422, "Choose a container metric")
    return unconfigured_metric(server, metric, start) or metrics_result(
        metric, container.server_id, container.docker_id, start, end, step
    )
