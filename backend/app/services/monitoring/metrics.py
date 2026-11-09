"""Metric expressions and sample availability, independent of rule publishing."""

import json
import math
import re
from uuid import UUID

from fastapi import HTTPException


def metric_expression(metric: str, server_id: UUID | None = None, docker_id: str | None = None):
    labels = []
    if server_id:
        labels.append(f"server_id={json.dumps(str(server_id))}")
    if docker_id:
        if metric in ("container_restarts_5m", "container_oom_5m", "container_unhealthy"):
            match = re.search(r"([a-f0-9]{64})(?:\.scope)?$", docker_id)
            if not match:
                raise HTTPException(422, "Container has no Docker identity")
            labels.append(f"docker_id={json.dumps(match.group(1))}")
        else:
            labels.append(f"id={json.dumps(docker_id)}")

    def selector(*extra):
        return "{" + ",".join([*labels, *extra]) + "}"

    node = selector('job="node"')
    container = selector('job="cadvisor"', 'image!=""')
    filesystem = selector('job="node"', 'fstype!~"tmpfs|overlay|squashfs|nsfs"')
    operations = (
        f"rate(node_disk_reads_completed_total{node}[2m]) + "
        f"rate(node_disk_writes_completed_total{node}[2m])"
    )
    expressions = {
        "host_cpu_percent": (
            "100 * (1 - avg by (server_id, source_epoch) (rate(node_cpu_seconds_total"
            + selector('job="node"', 'mode="idle"')
            + "[2m])))"
        ),
        "host_memory_percent": (
            f"100 * (1 - node_memory_MemAvailable_bytes{node} / node_memory_MemTotal_bytes{node})"
        ),
        "host_disk_percent": (
            "100 * (1 - node_filesystem_avail_bytes"
            + selector('job="node"', 'fstype!~"tmpfs|overlay|squashfs|nsfs"')
            + " / node_filesystem_size_bytes"
            + selector('job="node"', 'fstype!~"tmpfs|overlay|squashfs|nsfs"')
            + ")"
        ),
        "host_network_receive_bytes": f"rate(node_network_receive_bytes_total{node}[2m])",
        "host_network_transmit_bytes": f"rate(node_network_transmit_bytes_total{node}[2m])",
        "host_load1": f"node_load1{node}",
        "host_iowait_percent": "100 * avg by (server_id, source_epoch) (rate(node_cpu_seconds_total"
        + selector('job="node"', 'mode="iowait"')
        + "[2m]))",
        "host_memory_available_bytes": f"node_memory_MemAvailable_bytes{node}",
        "host_swap_percent": (
            f"100 * (1 - node_memory_SwapFree_bytes{node} / node_memory_SwapTotal_bytes{node}) "
            f"and (node_memory_SwapTotal_bytes{node} > 0)"
        ),
        "host_disk_available_bytes": f"node_filesystem_avail_bytes{filesystem}",
        "host_inode_percent": (
            f"100 * (1 - node_filesystem_files_free{filesystem} / "
            f"node_filesystem_files{filesystem}) and (node_filesystem_files{filesystem} > 0)"
        ),
        "host_disk_read_bytes": f"rate(node_disk_read_bytes_total{node}[2m])",
        "host_disk_write_bytes": f"rate(node_disk_written_bytes_total{node}[2m])",
        "host_disk_io_wait_seconds": (
            f"(rate(node_disk_read_time_seconds_total{node}[2m]) + "
            f"rate(node_disk_write_time_seconds_total{node}[2m])) / ({operations}) "
            f"and (({operations}) > 0)"
        ),
        "host_network_receive_errors": f"rate(node_network_receive_errs_total{node}[2m])",
        "host_network_transmit_errors": f"rate(node_network_transmit_errs_total{node}[2m])",
        "host_network_receive_drops": f"rate(node_network_receive_drop_total{node}[2m])",
        "host_network_transmit_drops": f"rate(node_network_transmit_drop_total{node}[2m])",
        "http_latency_seconds": "probe_duration_seconds" + selector('job="blackbox"'),
        "container_restarts_5m": "docker_container_restarts_5m"
        + selector('job="docker-state"')
        + " and on(server_id, instance) (docker_state_window_complete"
        + selector('job="docker-state"')
        + " == 1)",
        "container_oom_5m": "docker_container_oom_5m"
        + selector('job="docker-state"')
        + " and on(server_id, instance) (docker_state_window_complete"
        + selector('job="docker-state"')
        + " == 1)",
        "container_unhealthy": "docker_container_unhealthy"
        + selector('job="docker-state"')
        + " and on(server_id, source_epoch, instance) (docker_state_up"
        + selector('job="docker-state"')
        + " == 1) and on(server_id, source_epoch, instance) (time() - "
        + "docker_state_last_poll_timestamp_seconds"
        + selector('job="docker-state"')
        + " < 30) and on(server_id, source_epoch, instance) (up"
        + selector('job="docker-state"')
        + " == 1)",
        "container_cpu_percent": (
            "100 * sum by (server_id, source_epoch, id, name, image) "
            f"(rate(container_cpu_usage_seconds_total{container}[2m]))"
        ),
        "container_memory_bytes": f"container_memory_working_set_bytes{container}",
        "container_network_receive_bytes": (
            f"rate(container_network_receive_bytes_total{container}[2m])"
        ),
        "container_network_transmit_bytes": (
            f"rate(container_network_transmit_bytes_total{container}[2m])"
        ),
        "target_up": "up"
        + selector('job=~"node|cadvisor"')
        + " or platform_inventory_up"
        + selector('job="inventory"'),
        "http_up": "probe_success" + selector('job="blackbox"'),
        "container_up": "platform_container_up" + selector('job="inventory"'),
    }
    if metric == "container_cpu_host_percent":
        idle = selector('job="node"', 'mode="idle"')
        node_health = selector('job="node"')
        cores = f"count by(server_id,source_epoch) (node_cpu_seconds_total{idle})"
        return (
            f"({expressions['container_cpu_percent']}) "
            f"/ on(server_id,source_epoch) group_left() ({cores}) "
            f"and on(server_id,source_epoch) (up{node_health} == 1)"
        )
    if metric == "container_cpu_quota_percent":
        quota = f"container_spec_cpu_quota{container}"
        period = f"container_spec_cpu_period{container}"
        return (
            f"({expressions['container_cpu_percent']}) / on(server_id,source_epoch,id) "
            f"group_left() (({quota} / {period}) and ({quota} > 0) and ({period} > 0))"
        )
    if metric == "container_memory_limit_bytes":
        limit = f"container_spec_memory_limit_bytes{container}"
        return f"{limit} and ({limit} > 0) and ({limit} < 1e18)"
    return expressions[metric]


def llm_metric_expression(metric, endpoint_id=None):
    labels = 'job="llm-probe"'
    if endpoint_id:
        labels += f',endpoint_id="{endpoint_id}"'
    selector = "{" + labels + "}"

    def series(name, extra=""):
        return name + "{" + labels + extra + "}"

    last_run = series("llm_probe_last_run_timestamp_seconds")
    interval = series("llm_probe_interval_seconds")
    fresh = f"(time() - {last_run} <= 2 * {interval})"
    known = series("llm_probe_has_result") + " == 1"
    requests = f"sum by (endpoint_id) (rate(llm_probe_requests_total{selector}[5m]))"
    success_series = series("llm_probe_requests_total", ',outcome="success"')
    error_series = series("llm_probe_requests_total", ',outcome!="success"')
    success = f"sum by (endpoint_id) (rate({success_series}[5m]))"
    expressions = {
        "llm_up": (
            f"{series('llm_probe_success')} and on(endpoint_id) {fresh} "
            f"and on(endpoint_id) ({known})"
        ),
        "llm_latency_seconds": (
            f"{series('llm_probe_latency_seconds')} and on(endpoint_id) ({known})"
        ),
        "llm_http_status": f"{series('llm_http_status')} and on(endpoint_id) ({known})",
        "llm_probe_stale": f"(time() - {last_run}) > bool (2 * {interval})",
        "llm_success_rate": f"({success}) / ({requests})",
        "llm_latency_avg_seconds": (
            f"rate(llm_request_duration_seconds_sum{selector}[5m]) / "
            f"rate(llm_request_duration_seconds_count{selector}[5m])"
        ),
        "llm_latency_p95_seconds": (
            "histogram_quantile(0.95, sum by (endpoint_id, le) "
            f"(rate(llm_request_duration_seconds_bucket{selector}[5m])))"
        ),
        "llm_error_rate": (
            f"sum by (endpoint_id, outcome) (rate({error_series}[5m])) "
            f"/ on(endpoint_id) group_left() ({requests})"
        ),
    }
    for error in ("authentication_failed", "insufficient_balance", "rate_limited", "service_error"):
        expressions["llm_" + error] = (
            f"sum without(error_type) ({series('llm_probe_error', f',error_type="{error}"')})"
            f" and on(endpoint_id) {fresh}"
        )
    return expressions[metric]


def metric_availability(data, start=None, end=None, step=None):
    def finite(value):
        try:
            return value is not None and math.isfinite(float(value))
        except (ValueError, TypeError):
            return False

    for series in data.get("result", []):
        if "values" in series:
            points = {round(float(t), 3): v if finite(v) else None for t, v in series["values"]}
            if start is not None:
                first = start.timestamp()
                series["values"] = [
                    [round(first + i * step, 3), points.get(round(first + i * step, 3))]
                    for i in range(int((end - start).total_seconds() // step) + 1)
                ]
            else:
                series["values"] = [[t, v] for t, v in points.items()]
    data["result"] = [
        s
        for s in data.get("result", [])
        if any(finite(p[1]) for p in s.get("values", []))
        or (s.get("value") and finite(s["value"][1]))
    ]
    data["availability"] = "available" if data["result"] else "unavailable"
    if not data["result"]:
        data["reason"] = "no_valid_samples"
    if start is not None:
        data.update(start=start.timestamp(), end=end.timestamp(), step=step)
        valid_times = [t for s in data["result"] for t, v in s.get("values", []) if finite(v)]
        data["last_sample_at"] = max(valid_times) if valid_times else None
        if valid_times and end.timestamp() - max(valid_times) > max(60, step * 2):
            data["availability"], data["reason"] = "stale", "data_stale"
    return data
