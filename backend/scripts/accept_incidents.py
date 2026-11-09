"""Batch 7: real local collector -> Prometheus -> Alertmanager -> incident lifecycle.

The temporary server points to the existing local collectors; no remote host is changed.
Only test-worker is stopped. Direct duplicate webhook replay is explicitly simulated.
"""

import json
import re
import subprocess
from datetime import UTC, datetime
from uuid import uuid4

from accept_monitoring import ROOT, request, wait_for


def compose(*args):
    subprocess.run(
        ["docker", "compose", *args], cwd=ROOT, check=True, capture_output=True, text=True
    )


def ready():
    try:
        return request("/health/ready")
    except (OSError, RuntimeError):
        return None


def main():
    credentials = {}
    for line in (ROOT / ".local/admin-credentials.txt").read_text(encoding="utf-8").splitlines():
        match = re.match(r"(username|password)\s*[:=]\s*(.+)", line)
        if match:
            credentials[match[1]] = match[2]
    token = request("/api/v1/auth/login", "POST", credentials)["access_token"]
    report = {"checked_at": datetime.now(UTC).isoformat(), "status": "running"}
    rule_ids = []
    server_id = None
    cleanup_errors = []
    suffix = uuid4().hex[:10]
    try:
        report["health"] = request("/health/ready")
        server = request(
            "/api/v1/servers",
            "POST",
            {
                "name": f"batch7-local-{suffix}",
                "host": "127.0.0.1",
                "environment": "batch7-local-acceptance",
                "node_exporter_target": "node-exporter:9100",
                "cadvisor_target": "cadvisor:8080",
            },
            token,
        )
        server_id = server["id"]
        report["server_id"] = server_id

        def inventory():
            try:
                return request(f"/api/v1/servers/{server_id}/sync-containers", "POST", token=token)
            except RuntimeError:
                return None

        containers = wait_for("temporary local collector registration", inventory)
        worker = next(
            item
            for item in containers
            if item["name"] == "sentinelops-test-worker-1" and item["status"] == "running"
        )
        report["container_id"] = worker["id"]
        # Only retire stale inventory belonging to this newly created acceptance asset.
        for item in containers:
            if item["status"] == "stopped":
                request(f"/api/v1/containers/{item['id']}", "DELETE", token=token)
        codes = [f"Batch7WorkerDownA_{suffix}", f"Batch7WorkerDownB_{suffix}"]
        for index, code in enumerate(codes):
            rule = request(
                "/api/v1/alert-rules",
                "POST",
                {
                    "code": code,
                    "metric": "container_up",
                    "threshold": 1,
                    "server_id": server_id,
                    "for_seconds": 15,
                    "severity": "warning" if index == 0 else "critical",
                },
                token,
            )
            rule_ids.append(rule["id"])
        request("/api/v1/monitoring/apply-rules", "POST", token=token)
        report["rule_ids"] = rule_ids
        fault_at = datetime.now(UTC)
        compose("stop", "test-worker")

        def firing():
            events = request(
                f"/api/v1/alerts?server_id={server_id}&status=firing&limit=100", token=token
            )
            events = [
                item
                for item in events
                if item["rule_id"] in rule_ids
                and item["labels"].get("container_id") == worker["id"]
                and datetime.fromisoformat(item["starts_at"].replace("Z", "+00:00")) >= fault_at
            ]
            return (
                events if len(events) == 2 and all(item["incident_id"] for item in events) else None
            )

        events = wait_for(
            "two real container alerts associated with an incident", firing, seconds=240
        )
        assert len({item["incident_id"] for item in events}) == 1
        incident_id = events[0]["incident_id"]
        report["incident_id"] = incident_id
        report["alert_ids"] = [item["id"] for item in events]
        report["real_alerts"] = events
        base = f"/api/v1/incidents/{incident_id}"
        detail = request(base, token=token)
        assert detail["alert_count"] == detail["firing_alert_count"] == 2
        assert detail["severity"] == "critical"
        actor = request("/api/v1/me", token=token)
        request(base, "PATCH", {"assignee_id": actor["id"]}, token)
        request(
            base + "/status",
            "POST",
            {"status": "in_progress", "comment": "Local test-worker outage under investigation"},
            token,
        )
        request(
            base + "/comments", "POST", {"comment": "Two rules describe this same outage"}, token
        )
        before = request(base + "/timeline?limit=100", token=token)
        service_token = (ROOT / ".local/alertmanager.token").read_text(encoding="utf-8").strip()
        duplicate = {
            "version": "4",
            "alerts": [
                {
                    "fingerprint": item["fingerprint"],
                    "status": "firing",
                    "startsAt": item["starts_at"],
                    "endsAt": "0001-01-01T00:00:00Z",
                    "labels": item["labels"],
                    "annotations": item["annotations"],
                }
                for item in events
            ],
        }
        for _ in range(2):
            assert (
                request("/api/v1/hooks/alertmanager", "POST", duplicate, service_token)["changed"]
                == 0
            )
        assert request(base + "/timeline?limit=100", token=token) == before
        report["simulated_duplicate_webhook"] = {
            "replays": 2,
            "changed": 0,
            "timeline_unchanged": True,
        }
        for item in events:
            request(
                f"/api/v1/alerts/{item['id']}/ack",
                "POST",
                {"comment": "Expected local fault"},
                token,
            )
        compose("start", "test-worker")

        def recovered():
            item = request(base, token=token)
            return (
                item if item["status"] == "recovered" and item["firing_alert_count"] == 0 else None
            )

        report["recovered"] = wait_for(
            "all real alerts recover the same incident", recovered, seconds=240
        )
        report["closed"] = request(
            base + "/status",
            "POST",
            {"status": "closed", "comment": "Container running; both alerts resolved"},
            token,
        )
        timeline = request(base + "/timeline?limit=100", token=token)
        assert sum(item["event_type"] == "alert_resolved" for item in timeline) == 2
        assert sum(item["event_type"] == "alert_acknowledged" for item in timeline) == 2
        assert all(
            item["actor_id"] == actor["id"] for item in timeline if item["actor_type"] == "user"
        )
        report["timeline"] = timeline
        compose("restart", "backend")
        wait_for("backend ready after restart", ready)
        after = request(base, token=token)
        assert after["status"] == "closed" and after["assignee_id"] == actor["id"]
        assert request(base + "/timeline?limit=100", token=token) == timeline
        report["restart_persistence"] = True
        report["status"] = "passed"
    except Exception as exc:
        report["status"] = "failed"
        report["error_type"] = type(exc).__name__
        raise
    finally:
        try:
            compose("start", "test-worker")
        except Exception as exc:
            cleanup_errors.append(f"restore worker: {type(exc).__name__}")
        for rule_id in rule_ids:
            try:
                request(f"/api/v1/alert-rules/{rule_id}", "DELETE", token=token)
            except Exception as exc:
                cleanup_errors.append(f"delete rule: {type(exc).__name__}")
        try:
            request("/api/v1/monitoring/apply-rules", "POST", token=token)
        except Exception as exc:
            cleanup_errors.append(f"apply rules: {type(exc).__name__}")
        if server_id:
            try:
                request(f"/api/v1/servers/{server_id}", "DELETE", token=token)
            except Exception as exc:
                cleanup_errors.append(f"retire temporary asset: {type(exc).__name__}")
        try:
            request("/api/v1/auth/logout", "POST", token=token)
        except Exception as exc:
            cleanup_errors.append(f"logout: {type(exc).__name__}")
        report["cleanup"] = {"complete": not cleanup_errors, "errors": cleanup_errors}
        if cleanup_errors:
            report["status"] = "failed"
        (ROOT / ".local/batch7-acceptance.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    if cleanup_errors:
        raise RuntimeError("Acceptance cleanup incomplete; see batch7-acceptance.json")
    print(f"Batch 7 real local acceptance passed: incident {report['incident_id']}", flush=True)


if __name__ == "__main__":
    main()
