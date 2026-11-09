"""Bounded page summaries. No raw history is copied into the business database."""

import json
import math
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

from fastapi import HTTPException

from app.integrations.prometheus import prometheus
from app.services.monitoring.metrics import metric_expression

HOST_METRICS = ("host_cpu_percent", "host_memory_percent", "host_disk_percent")


def sample(availability, reason=None, value=None, at=None):
    return {"availability": availability, "reason": reason, "value": value, "sampled_at": at}


def fetch(path, params=None):
    try:
        return prometheus(path, params, timeout_seconds=8)
    except HTTPException:
        return None


def server_summaries(servers):
    if not servers:
        return []
    enabled = [s for s in servers if s.monitoring_enabled]
    results = {}
    if enabled:
        selected = "server_id=~" + json.dumps("|".join(str(s.id) for s in enabled))
        expressions = {
            m: f'({metric_expression(m)}) and on(server_id) up{{job="node",{selected}}}'
            for m in HOST_METRICS
        }
        expressions.update(
            {
                "docker-state": f"docker_state_up{{{selected}}} and "
                f"(time() - docker_state_last_poll_timestamp_seconds{{{selected}}} < 30)",
                "blackbox": f'probe_success{{job="blackbox",{selected}}}',
            }
        )
        # Six bounded calls for any page size; independent failures remain local.
        with ThreadPoolExecutor(max_workers=6) as pool:
            pending = {
                k: pool.submit(fetch, "/api/v1/query", {"query": v, "timeout": "8s"})
                for k, v in expressions.items()
            }
            targets = pool.submit(fetch, "/api/v1/targets", {"state": "active"})
            results = {k: v.result() for k, v in pending.items()}
            results["targets"] = targets.result()
    output = []
    for server in servers:
        sid, epoch = str(server.id), server.collection_config_changed_at.isoformat()
        collectors = {}
        for job, target in (
            ("node", server.node_exporter_target),
            ("cadvisor", server.cadvisor_target),
            ("docker-state", server.docker_state_target),
            ("blackbox", server.health_url),
        ):
            if not server.monitoring_enabled:
                status = sample("paused", "monitoring_disabled")
            elif not target:
                status = sample("unconfigured", "target_not_configured")
            elif results.get("targets") is None:
                status = sample("unavailable", "prometheus_unavailable")
            else:
                matches = [
                    t
                    for t in results["targets"].get("activeTargets", [])
                    if t.get("labels", {}).get("server_id") == sid
                    and t["labels"].get("source_epoch") == epoch
                    and t["labels"].get("job") == job
                ]
                t = matches[0] if matches else None
                try:
                    at = (
                        datetime.fromisoformat(t["lastScrape"].replace("Z", "+00:00"))
                        if t
                        else None
                    )
                except (ValueError, KeyError):
                    at = None
                if not t:
                    status = sample("unavailable", "awaiting_discovery")
                elif not at or not -5 <= (datetime.now(UTC) - at).total_seconds() <= 60:
                    status = sample("stale", "scrape_stale", at=at)
                elif t.get("health") != "up":
                    status = sample("unavailable", "scrape_failed", 0, at)
                else:
                    status = sample("available", value=1, at=at)
                    if job in ("docker-state", "blackbox"):
                        data = results.get(job)
                        values = [
                            float(s["value"][1])
                            for s in (data or {}).get("result", [])
                            if s.get("metric", {}).get("server_id") == sid
                            and s["metric"].get("source_epoch") == epoch
                        ]
                        if data is None:
                            status = sample("unavailable", "query_failed", at=at)
                        elif not values:
                            status = sample("stale", "source_missing_or_stale", at=at)
                        elif not all(v == 1 for v in values):
                            status = sample(
                                "unavailable",
                                "engine_unavailable" if job == "docker-state" else "probe_failed",
                                0,
                                at,
                            )
            collectors[job] = {**status, "target": target}
        metrics = {}
        for metric in HOST_METRICS:
            node = collectors["node"]
            if node["availability"] != "available":
                metrics[metric] = {k: v for k, v in node.items() if k != "target"}
                metrics[metric]["value"] = None
                continue
            data = results.get(metric)
            values = [
                float(s["value"][1])
                for s in (data or {}).get("result", [])
                if s.get("metric", {}).get("server_id") == sid
                and s["metric"].get("source_epoch") == epoch
                and math.isfinite(float(s["value"][1]))
            ]
            metrics[metric] = (
                sample("available", value=max(values), at=node["sampled_at"])
                if values
                else sample("unavailable", "query_failed" if data is None else "no_valid_samples")
            )
        required = [collectors[j] for j in ("node", "cadvisor")]
        metrics["target_up"] = (
            sample("available", value=1, at=min(s["sampled_at"] for s in required))
            if all(s["availability"] == "available" for s in required)
            else sample("unavailable", "collector_unavailable", 0)
            if any(s["value"] == 0 for s in required)
            else sample(
                required[0]["availability"] if not server.monitoring_enabled else "unavailable",
                "collector_unknown",
            )
        )
        output.append({"server_id": server.id, "metrics": metrics, "collectors": collectors})
    return output


def container_health(server, docker_id):
    if not server.monitoring_enabled:
        return {"status": "unknown", **sample("paused", "monitoring_disabled")}
    if not server.docker_state_target:
        return {"status": "unknown", **sample("unconfigured", "docker_state_not_configured")}
    import re

    match = re.fullmatch(
        r"(?:/docker/|/system.slice/docker-)?([a-f0-9]{64})(?:\.scope)?", docker_id
    )
    if not match:
        return {"status": "unknown", **sample("unavailable", "docker_identity_unavailable")}
    labels = (
        f'job="docker-state",server_id="{server.id}",'
        f"source_epoch={json.dumps(server.collection_config_changed_at.isoformat())}"
    )
    alive = (
        f"(docker_state_up{{{labels}}} == 1) and "
        f"(up{{{labels}}} == 1) and (time() - timestamp(up{{{labels}}}) < 60)"
    )
    poll = fetch(
        "/api/v1/query",
        {
            "query": f"docker_state_last_poll_timestamp_seconds{{{labels}}} and ({alive})",
            "timeout": "8s",
        },
    )
    times = [float(s["value"][1]) for s in (poll or {}).get("result", [])]
    now = datetime.now(UTC).timestamp()
    if not times or not all(math.isfinite(t) and -5 <= now - t < 30 for t in times):
        return {
            "status": "unknown",
            **sample("stale" if times else "unavailable", "source_missing_or_stale"),
        }
    data = fetch(
        "/api/v1/query",
        {
            "query": f'docker_container_health_status{{{labels},docker_id="{match[1]}"}}',
            "timeout": "8s",
        },
    )
    states = {
        s.get("metric", {}).get("health")
        for s in (data or {}).get("result", [])
        if float(s["value"][1]) == 1
    }
    state = next(iter(states)) if len(states) == 1 else "unknown"
    if state not in ("healthy", "unhealthy", "starting", "none", "stopped"):
        state = "unknown"
    return {
        "status": state,
        "availability": "unconfigured"
        if state == "none"
        else "available"
        if state != "unknown"
        else "unavailable",
        "sampled_at": datetime.fromtimestamp(min(times), UTC),
        "reason": "healthcheck_not_configured"
        if state == "none"
        else "no_valid_samples"
        if state == "unknown"
        else None,
    }
