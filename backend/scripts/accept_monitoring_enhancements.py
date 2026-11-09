"""Real disposable Docker HEALTHCHECK failure, signal loss and verified recovery."""

import json
import re
import subprocess
from datetime import UTC, datetime
from urllib.parse import urlencode
from uuid import uuid4

from accept_monitoring import ROOT, request, wait_for

FILE = "backend/monitoring-enhancements-test-compose.yaml"


def compose(*args):
    result = subprocess.run(
        ["docker", "compose", "-f", FILE, *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def docker_state(identifier):
    result = subprocess.run(
        ["docker", "inspect", "--format", "{{json .State}}", identifier],
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(result.stdout)


def main():
    credentials = {}
    for line in (ROOT / ".local/admin-credentials.txt").read_text(encoding="utf-8").splitlines():
        match = re.match(r"(username|password)\s*[:=]\s*(.+)", line)
        if match:
            credentials[match[1]] = match[2]
    token = request("/api/v1/auth/login", "POST", credentials)["access_token"]
    report = {
        "started_at": datetime.now(UTC).isoformat(),
        "passed": False,
        "cleanup": False,
        "errors": [],
    }
    server, rule, event = None, None, None
    try:
        compose("up", "-d")
        identifier = compose("ps", "-q", "health-target")
        wait_for(
            "Docker healthy baseline",
            lambda: docker_state(identifier)["Health"]["Status"] == "healthy",
            30,
        )
        server = request(
            "/api/v1/servers",
            "POST",
            {
                "name": "monitoring-enhancements-" + uuid4().hex[:10],
                "host": "127.0.0.1",
                "environment": "isolated-local-acceptance",
                "node_exporter_target": "node-exporter:9100",
                "cadvisor_target": "cadvisor:8080",
                "docker_state_target": "monitoring-enhancements-collector:9400",
            },
            token,
        )
        sid = server["id"]
        report["server_id"], report["docker_id"] = sid, identifier

        def metric(name):
            return request(f"/api/v1/servers/{sid}/metrics?metric={name}", token=token)

        wait_for(
            "continuous health before five-minute lifecycle window",
            lambda: [
                s
                for s in metric("container_unhealthy")["result"]
                if s["metric"].get("docker_id") == identifier
            ],
            90,
        )
        prom = request(
            "/api/v1/query?"
            + urlencode({"query": f'docker_state_window_complete{{server_id="{sid}"}}'}),
            base="http://127.0.0.1:9090",
        )
        assert prom["data"]["result"][0]["value"][1] == "0"
        report["health_independent_of_5m_window"] = True

        def inventory():
            try:
                result = request(f"/api/v1/servers/{sid}/sync-containers", "POST", token=token)
                return next((c for c in result if identifier in c["docker_id"]), None)
            except RuntimeError:
                return None

        container = wait_for("fresh cAdvisor inventory", inventory, 90)
        cid = container["id"]
        report["container_id"] = cid

        def health():
            return request(f"/api/v1/containers/{cid}/health", token=token)

        report["healthy"] = wait_for(
            "API healthy baseline",
            lambda: h if (h := health())["status"] == "healthy" else None,
            30,
        )
        summary = request(f"/api/v1/monitoring/servers-summary?server_ids={sid}", token=token)[0]
        assert summary["collectors"]["docker-state"]["availability"] == "available"
        assert summary["collectors"]["blackbox"]["availability"] == "unconfigured"
        report["summary"] = summary
        rule = request(
            "/api/v1/alert-rules",
            "POST",
            {
                "code": "HealthAcceptance_" + uuid4().hex[:10],
                "server_id": sid,
                "metric": "container_unhealthy",
                "threshold": 0,
                "for_seconds": 0,
                "severity": "critical",
            },
            token,
        )
        request("/api/v1/monitoring/apply-rules", "POST", token=token)
        compose("exec", "-T", "health-target", "touch", "/tmp/health-failure")
        wait_for(
            "running but Docker unhealthy",
            lambda: docker_state(identifier)["Health"]["Status"] == "unhealthy",
            30,
        )
        state = docker_state(identifier)
        assert state["Running"] is True
        report["docker_running_while_unhealthy"] = True
        report["unhealthy"] = wait_for(
            "API unhealthy", lambda: h if (h := health())["status"] == "unhealthy" else None, 45
        )

        def occurrence():
            records = request(
                "/api/v1/alerts?" + urlencode({"server_id": sid, "limit": 100}), token=token
            )
            return next(
                (
                    e
                    for e in records
                    if e["rule_id"] == rule["id"] and e["labels"].get("docker_id") == identifier
                ),
                None,
            )

        event = wait_for("health alert linked to incident", occurrence, 120)
        assert event["status"] == "firing" and event["incident_id"]
        report["firing_event"] = event
        print("Health failure produced a real alert and incident", flush=True)
        compose("stop", "collector")
        report["collector_loss"] = wait_for(
            "loss never appears healthy",
            lambda: h if (h := health())["status"] == "unknown" else None,
            45,
        )

        def pending():
            e = request("/api/v1/alerts/" + event["id"], token=token)
            return (
                e
                if e["status"] == "resolved"
                and e["recovery_status"] == "pending"
                and e["recovery_checked_at"]
                else None
            )

        report["missing_signal_pending"] = wait_for(
            "resolved signal is not recovery evidence", pending, 120
        )
        incident = request("/api/v1/incidents/" + event["incident_id"], token=token)
        assert incident["status"] == "open" and not incident["recovered_at"]
        assert not metric("container_unhealthy")["result"]
        print("Collector loss left incident open with pending recovery", flush=True)
        compose("exec", "-T", "health-target", "rm", "/tmp/health-failure")
        wait_for(
            "Docker healthy again",
            lambda: docker_state(identifier)["Health"]["Status"] == "healthy",
            30,
        )
        compose("start", "collector")
        report["healthy_again"] = wait_for(
            "fresh API healthy", lambda: h if (h := health())["status"] == "healthy" else None, 90
        )

        def verified():
            e = request("/api/v1/alerts/" + event["id"], token=token)
            return e if e["recovery_status"] == "verified" else None

        report["verified_event"] = wait_for(
            "fresh normal evidence verifies recovery", verified, 150
        )
        incident = request("/api/v1/incidents/" + event["incident_id"], token=token)
        assert incident["status"] == "recovered" and incident["recovered_at"]
        report["recovered_incident"] = incident
        report["passed"] = True
        print("Fresh healthy evidence recovered the incident", flush=True)
    except Exception as exc:
        report["errors"].append(f"{type(exc).__name__}: {exc}")
        raise
    finally:
        cleanup_errors = []
        if event and report["passed"]:
            try:
                request(
                    "/api/v1/incidents/" + event["incident_id"] + "/status",
                    "POST",
                    {"status": "closed", "comment": "本地健康故障、采集中断与恢复验收完成"},
                    token,
                )
            except Exception as exc:
                cleanup_errors.append(str(exc))
        if rule:
            try:
                request("/api/v1/alert-rules/" + rule["id"], "DELETE", token=token)
                request("/api/v1/monitoring/apply-rules", "POST", token=token)
            except Exception as exc:
                cleanup_errors.append(str(exc))
        if server:
            try:
                request("/api/v1/servers/" + server["id"], "DELETE", token=token)
            except Exception as exc:
                cleanup_errors.append(str(exc))
        try:
            compose("down")
        except Exception as exc:
            cleanup_errors.append(str(exc))
        report["cleanup"] = not cleanup_errors
        report["errors"].extend(cleanup_errors)
        report["finished_at"] = datetime.now(UTC).isoformat()
        (ROOT / ".local/monitoring-enhancements-acceptance.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(
            json.dumps({k: report[k] for k in ("passed", "cleanup", "errors")}, ensure_ascii=False),
            flush=True,
        )
        if cleanup_errors:
            raise RuntimeError("Acceptance cleanup incomplete")


if __name__ == "__main__":
    main()
