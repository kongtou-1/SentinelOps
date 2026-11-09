"""Local bounded restart/OOM, HTTP missing-signal recovery and Mailpit acceptance."""

import json
import re
import subprocess
from datetime import UTC, datetime
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from uuid import uuid4

from accept_monitoring import ROOT, request, wait_for

FILE = "backend/completion-test-compose.yaml"


def compose(*args, base=False):
    command = ["docker", "compose"]
    if not base:
        command += ["-f", FILE]
    result = subprocess.run([*command, *args], cwd=ROOT, check=True, capture_output=True, text=True)
    return result.stdout.strip()


def control(path, port=18003):
    try:
        with urlopen(Request(f"http://127.0.0.1:{port}" + path, method="POST"), timeout=5):
            pass
    except (OSError, HTTPError, URLError):
        if path != "/oom":
            raise


def metrics(server_id, metric, token):
    return request(
        f"/api/v1/servers/{server_id}/metrics?" + urlencode({"metric": metric}), token=token
    )


def state(identifier):
    return json.loads(
        subprocess.run(
            ["docker", "inspect", "--format", "{{json .State}}", identifier],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
    )


def main():
    credentials = {}
    for line in (ROOT / ".local/admin-credentials.txt").read_text(encoding="utf-8").splitlines():
        match = re.match(r"(username|password)\s*[:=]\s*(.+)", line)
        if match:
            credentials[match[1]] = match[2]
    token = request("/api/v1/auth/login", "POST", credentials)["access_token"]
    stamp = uuid4().hex[:10]
    evidence = {
        "started_at": datetime.now(UTC).isoformat(),
        "passed": False,
        "cleanup": False,
        "metrics": {},
        "events": {},
        "errors": [],
    }
    server, rules = None, []
    try:
        compose("up", "-d")
        wait_for("disposable HTTP target ready", http_ready, seconds=30)
        server = request(
            "/api/v1/servers",
            "POST",
            {
                "name": "completion-" + stamp,
                "host": "127.0.0.1",
                "environment": "isolated-local-acceptance",
                "node_exporter_target": "node-exporter:9100",
                "cadvisor_target": "cadvisor:8080",
                "docker_state_target": "docker-state:9400",
                "health_url": "http://completion-latency:8003",
            },
            token,
        )
        server_id = server["id"]
        added = [
            "host_load1",
            "host_iowait_percent",
            "host_memory_available_bytes",
            "host_swap_percent",
            "host_disk_available_bytes",
            "host_inode_percent",
            "host_disk_read_bytes",
            "host_disk_write_bytes",
            "host_disk_io_wait_seconds",
            "host_network_receive_errors",
            "host_network_transmit_errors",
            "host_network_receive_drops",
            "host_network_transmit_drops",
            "http_latency_seconds",
        ]
        wait_for(
            "node exporter service discovery",
            lambda: metrics(server_id, "host_load1", token)["result"],
            seconds=90,
        )
        wait_for(
            "two fresh scrapes for counter rates",
            lambda: metrics(server_id, "host_iowait_percent", token)["result"],
            seconds=90,
        )
        for metric in added:
            evidence["metrics"][metric] = metrics(server_id, metric, token)
        assert evidence["metrics"]["host_load1"]["result"]
        assert evidence["metrics"]["http_latency_seconds"]["result"]
        for code, metric, threshold in (
            ("Restart", "container_restarts_5m", 0),
            ("OOM", "container_oom_5m", 0),
            ("HTTPDelay", "http_latency_seconds", 0.2),
        ):
            rules.append(
                request(
                    "/api/v1/alert-rules",
                    "POST",
                    {
                        "code": "Completion" + code + stamp,
                        "server_id": server_id,
                        "metric": metric,
                        "threshold": threshold,
                        "for_seconds": 0,
                    },
                    token,
                )
            )
        request("/api/v1/monitoring/apply-rules", "POST", token=token)
        wait_for(
            "complete Docker lifecycle observation window",
            lambda: metrics(server_id, "container_restarts_5m", token)["result"],
            seconds=360,
        )
        baseline_mail = {
            m["ID"] for m in request("/api/v1/messages", base="http://127.0.0.1:8025")["messages"]
        }
        identifier = compose("ps", "-q", "oom-target")
        compose("restart", "oom-target")

        def lifecycle(metric):
            data = metrics(server_id, metric, token)
            return next(
                (
                    s
                    for s in data["result"]
                    if s["metric"].get("docker_id") == identifier and float(s["value"][1]) > 0
                ),
                None,
            )

        evidence["restart"] = wait_for(
            "Engine lifecycle restart metric",
            lambda: lifecycle("container_restarts_5m"),
            seconds=60,
        )
        control("/oom", 18004)
        evidence["oom_state"] = wait_for(
            "bounded-memory target is OOMKilled",
            lambda: state(identifier) if state(identifier).get("OOMKilled") else None,
            seconds=60,
        )
        evidence["oom"] = wait_for(
            "Engine OOM metric", lambda: lifecycle("container_oom_5m"), seconds=60
        )
        compose("start", "oom-target")
        control("/slow")

        def firing(code):
            return next(
                (
                    e
                    for e in request(
                        "/api/v1/alerts?" + urlencode({"server_id": server_id, "limit": 100}),
                        token=token,
                    )
                    if e["labels"].get("alertname") == code and e["status"] == "firing"
                ),
                None,
            )

        for rule in rules:
            evidence["events"][rule["metric"]] = wait_for(
                "Prometheus webhook " + rule["metric"],
                lambda rule=rule: firing(rule["code"]),
                seconds=120,
            )
        delay_event = evidence["events"]["http_latency_seconds"]
        compose("stop", "blackbox", base=True)

        def resolved_pending():
            event = request("/api/v1/alerts/" + delay_event["id"], token=token)
            return event if event["status"] == "resolved" and event["recovery_checked_at"] else None

        pending = wait_for(
            "HTTP disappearance ends notification but fails recovery check",
            resolved_pending,
            seconds=180,
        )
        assert pending["recovery_status"] == "pending", pending
        incident = request("/api/v1/incidents/" + pending["incident_id"], token=token)
        assert incident["status"] in ("open", "in_progress") and incident["recovered_at"] is None
        evidence["http_missing"] = {"event": pending, "incident": incident}
        control("/normal")
        compose("start", "blackbox", base=True)

        def verified(event):
            current = request("/api/v1/alerts/" + event["id"], token=token)
            return current if current["recovery_status"] == "verified" else None

        evidence["http_verified"] = wait_for(
            "new normal HTTP probe verifies recovery", lambda: verified(delay_event), seconds=180
        )
        for metric in ("container_restarts_5m", "container_oom_5m"):
            event = evidence["events"][metric]
            evidence[metric + "_verified"] = wait_for(
                "expired fault window plus fresh healthy collector " + metric,
                lambda event=event: verified(event),
                seconds=360,
            )
        incidents = {e["incident_id"] for e in evidence["events"].values()}
        for incident_id in incidents:

            def incident_recovered():
                row = request("/api/v1/incidents/" + incident_id, token=token)
                return row if row["status"] == "recovered" and row["recovered_at"] else None

            row = wait_for(
                "all correlated restart events have normal evidence",
                incident_recovered,
                seconds=360,
            )
            assert row["status"] == "recovered" and row["recovered_at"]
            request(
                "/api/v1/incidents/" + incident_id + "/status",
                "POST",
                {
                    "status": "closed",
                    "comment": "本地重启、有限内存 OOM 与 HTTP 缺失后正常证据核验完成",
                },
                token,
            )
        messages = wait_for(
            "new local fault and resolved emails",
            lambda: new_mail(baseline_mail, rules),
            seconds=120,
        )
        evidence["mailpit"] = messages
        evidence["passed"] = True
    except Exception as exc:
        evidence["errors"].append(type(exc).__name__ + ": " + str(exc))
        raise
    finally:
        try:
            compose("start", "docker-state", base=True)
            compose("start", "blackbox", base=True)
            for rule in rules:
                request("/api/v1/alert-rules/" + rule["id"], "DELETE", token=token)
            if server:
                request("/api/v1/servers/" + server["id"], "DELETE", token=token)
            request("/api/v1/monitoring/apply-rules", "POST", token=token)
            compose("down")
            evidence["cleanup"] = True
        except Exception as exc:
            evidence["errors"].append("cleanup: " + type(exc).__name__)
        (ROOT / ".local/completion-acceptance.json").write_text(
            json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    print("Local completion acceptance passed; disposable resources cleaned up")


def http_ready():
    try:
        with urlopen("http://127.0.0.1:18003/", timeout=3) as response:
            return response.status == 200
    except OSError:
        return False


def new_mail(baseline, rules):
    messages = request("/api/v1/messages", base="http://127.0.0.1:8025")["messages"]
    new = [
        m
        for m in messages
        if m["ID"] not in baseline and any(r["code"] in m["Subject"] for r in rules)
    ]
    return (
        [{"id": m["ID"], "subject": m["Subject"]} for m in new]
        if any("FIRING" in m["Subject"] for m in new)
        and any("RESOLVED" in m["Subject"] for m in new)
        else None
    )


if __name__ == "__main__":
    main()
