"""Current observations for one host, using a fixed number of Prometheus queries."""

import json
import math
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

from app.services.monitoring.metrics import metric_expression
from app.services.monitoring.summary import fetch
from app.utils.docker import docker_identifier


def overview(server, containers):
    now = datetime.now(UTC)
    output = {
        "server_id": server.id,
        "collected_at": now,
        "containers": containers,
        "observations": [{"container_id": c.id} for c in containers],
    }
    if not server.monitoring_enabled:
        return output
    labels = f'server_id="{server.id}",source_epoch=' + json.dumps(
        server.collection_config_changed_at.isoformat()
    )
    node = '{job="node",' + labels + "}"
    cores = f'count by (server_id, source_epoch) (node_cpu_seconds_total{node[:-1]},mode="idle"}})'
    expressions = {
        "cpu": f"({metric_expression('container_cpu_percent', server.id)}) "
        f"/ on(server_id, source_epoch) group_left() ({cores})",
        "memory": metric_expression("container_memory_bytes", server.id),
        "host_cpu": metric_expression("host_cpu_percent", server.id),
        "total": f"node_memory_MemTotal_bytes{node}",
        "used": f"node_memory_MemTotal_bytes{node} - node_memory_MemAvailable_bytes{node}",
    }
    recent_containers = f'(time() - container_last_seen{{job="cadvisor",{labels},image!=""}} < 60)'
    for key in ("cpu", "memory"):
        expressions[key] = (
            f"({expressions[key]}) and on(server_id, source_epoch, id) {recent_containers}"
        )
    for key in list(expressions):
        job = "cadvisor" if key in ("cpu", "memory") else "node"
        guard = '{job="' + job + '",' + labels + "}"
        expressions[key] = (
            f"({expressions[key]}) and on(server_id, source_epoch) (up{guard} == 1) "
            f"and on(server_id, source_epoch) (time() - timestamp(up{guard}) < 60)"
        )
    if server.docker_state_target:
        source = '{job="docker-state",' + labels + "}"
        guard = (
            f"and on(server_id, source_epoch) (up{source} == 1) "
            f"and on(server_id, source_epoch) (docker_state_up{source} == 1) "
            f"and on(server_id, source_epoch) "
            f"(time() - docker_state_last_poll_timestamp_seconds{source} < 30)"
        )
        expressions.update(
            {
                key: f"{metric}{source} {guard}"
                for key, metric in (
                    ("state", "docker_container_state_info"),
                    ("health", "docker_container_health_status"),
                    ("networks", "docker_container_network_info"),
                    ("network_inventory", "docker_container_network_inventory"),
                    ("mounts", "docker_container_mount_info"),
                    ("mount_inventory", "docker_container_mount_inventory"),
                )
            }
        )
    with ThreadPoolExecutor(max_workers=6) as pool:
        pending = {
            k: pool.submit(fetch, "/api/v1/query", {"query": v, "timeout": "8s"})
            for k, v in expressions.items()
        }
        data = {k: value.result() for k, value in pending.items()}

    def samples(key, identifier=None):
        result = []
        for item in (data.get(key) or {}).get("result", []):
            metric, pair = item.get("metric", {}), item.get("value", [])
            if metric.get("server_id") != str(server.id) or metric.get("source_epoch") != (
                server.collection_config_changed_at.isoformat()
            ):
                continue
            if identifier and docker_identifier(metric.get("id", "")) != identifier:
                continue
            try:
                at, value = float(pair[0]), float(pair[1])
            except (IndexError, ValueError, TypeError):
                continue
            if math.isfinite(value) and value >= 0 and -5 <= now.timestamp() - at <= 60:
                result.append((metric, value, datetime.fromtimestamp(at, UTC)))
        return result

    def scalar(key, identifier=None):
        values = samples(key, identifier)
        return max(x[1] for x in values) if values else None

    output.update(
        host_cpu_percent=scalar("host_cpu"),
        host_memory_total_bytes=scalar("total"),
        host_memory_used_bytes=scalar("used"),
    )
    for container, observation in zip(containers, output["observations"], strict=True):
        identifier = docker_identifier(container.docker_id)
        if not identifier:
            continue
        observation.update(
            cpu_host_percent=scalar("cpu", identifier), memory_bytes=scalar("memory", identifier)
        )
        for key in ("state", "health"):
            values = [x for x in samples(key, identifier) if x[1] == 1]
            states = {x[0].get(key) for x in values}
            if len(states) == 1:
                observation[key] = states.pop()
                observation["sampled_at"] = min(x[2] for x in values)
        if samples("network_inventory", identifier):
            observation["networks"] = sorted(
                {
                    x[0]["network"]
                    for x in samples("networks", identifier)
                    if x[1] == 1 and isinstance(x[0].get("network"), str)
                }
            )
        if samples("mount_inventory", identifier):
            observation["mounts"] = [
                {k: labels.get(k, "") for k in ("source", "destination", "type", "read_only")}
                for labels, value, _ in samples("mounts", identifier)
                if value == 1
            ]
    return output
