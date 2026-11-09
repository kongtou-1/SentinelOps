"""Real local acceptance. --fault-test only stops the disposable no-HTTP test-worker."""

import argparse
import json
import re
import subprocess
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[2]
API = "http://127.0.0.1:8000"


def request(path, method="GET", body=None, token=None, base=API):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = json.dumps(body).encode() if body is not None else None
    req = Request(base + path, data=data, headers=headers, method=method)
    try:
        with urlopen(req, timeout=15) as response:
            raw = response.read()
    except HTTPError as exc:
        raise RuntimeError(f"{method} {path}: HTTP {exc.code}") from None
    return json.loads(raw) if raw else None


def wait_for(description, check, seconds=120):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        result = check()
        if result:
            return result
        time.sleep(5)
    raise RuntimeError(f"Timed out: {description}")


def compose(action):
    subprocess.run(
        ["docker", "compose", action, "test-worker"], cwd=ROOT, check=True, capture_output=True
    )


def email_for_event(messages, baseline, event, status, fetch):
    """Require a new email containing this alert occurrence, not just its subject."""
    start = int(datetime.fromisoformat(event["starts_at"].replace("Z", "+00:00")).timestamp())
    for message in messages:
        if message["ID"] in baseline or status.upper() not in message["Subject"]:
            continue
        if event["labels"]["alertname"] not in message["Subject"]:
            continue
        detail = fetch(message["ID"])
        body = detail.get("HTML", "") + detail.get("Text", "")
        if all(
            value in body
            for value in (
                event["server_id"],
                event["labels"]["container_id"],
                f"event-start={start}",
            )
        ):
            return {"id": message["ID"], "subject": message["Subject"]}
    return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--fault-test", action="store_true")
    args = parser.parse_args()
    credentials = {}
    for line in (ROOT / ".local/admin-credentials.txt").read_text(encoding="utf-8").splitlines():
        match = re.match(r"(username|password)\s*[:=]\s*(.+)", line)
        if match:
            credentials[match[1]] = match[2]
    tokens = request("/api/v1/auth/login", "POST", credentials)
    token = tokens["access_token"]
    report = {"checked_at": datetime.now(UTC).isoformat(), "health": request("/health/ready")}
    try:
        servers = request("/api/v1/servers", token=token)
        server = next((s for s in servers if s["name"] == "local-docker-test"), None)
        body = {
            "name": "local-docker-test",
            "host": "127.0.0.1",
            "environment": "local-test",
            "node_exporter_target": "node-exporter:9100",
            "cadvisor_target": "cadvisor:8080",
            "health_url": "http://test-app:8001",
        }
        if server is None:
            server = request("/api/v1/servers", "POST", body, token)
        server_id = server["id"]
        report["server_id"] = server_id
        rules = request("/api/v1/alert-rules", token=token)
        for code, metric, threshold in (
            ("LocalHostCPUHigh", "host_cpu_percent", 85),
            ("LocalHostMemoryHigh", "host_memory_percent", 85),
            ("LocalHostDiskHigh", "host_disk_percent", 85),
            ("LocalExporterDown", "target_up", 1),
            ("LocalTestAppDown", "http_up", 1),
            ("LocalWorkerDown", "container_up", 1),
        ):
            if not any(r["code"] == code for r in rules):
                request(
                    "/api/v1/alert-rules",
                    "POST",
                    {
                        "code": code,
                        "metric": metric,
                        "threshold": threshold,
                        "server_id": server_id,
                        "for_seconds": 15,
                    },
                    token,
                )
        report["rules"] = request("/api/v1/monitoring/apply-rules", "POST", token=token)
        metrics = (
            "host_cpu_percent",
            "host_memory_percent",
            "host_disk_percent",
            "host_network_receive_bytes",
            "host_network_transmit_bytes",
            "container_cpu_percent",
            "container_memory_bytes",
            "container_network_receive_bytes",
            "container_network_transmit_bytes",
            "http_up",
            "container_up",
        )

        def metric_samples():
            results = {}
            for metric in metrics:
                data = request(f"/api/v1/servers/{server_id}/metrics?metric={metric}", token=token)
                results[metric] = {
                    "series": len(data["result"]),
                    "sample": data["result"][0]["value"][1] if data["result"] else None,
                }
            return results if all(result["series"] for result in results.values()) else None

        report["metrics"] = wait_for("host and container samples", metric_samples)
        now = datetime.now(UTC)
        params = urlencode(
            {
                "metric": "host_memory_percent",
                "start": (now - timedelta(minutes=2)).isoformat(),
                "end": now.isoformat(),
                "step": 15,
            }
        )
        history = request(f"/api/v1/servers/{server_id}/metrics?{params}", token=token)
        assert history["resultType"] == "matrix" and history["result"]
        report["range_points"] = len(history["result"][0]["values"])
        inventory = request(f"/api/v1/servers/{server_id}/sync-containers", "POST", token=token)
        assert inventory
        report["containers"] = len(inventory)
        inventory_again = request(
            f"/api/v1/servers/{server_id}/sync-containers", "POST", token=token
        )
        assert {c["id"] for c in inventory} == {c["id"] for c in inventory_again}
        report["container_sync_idempotent"] = True
        if args.fault_test:
            worker = next(
                c
                for c in inventory
                if c["name"] == "sentinelops-test-worker-1" and c["status"] == "running"
            )
            report["fault_container_id"] = worker["id"]
            mail_base = "http://127.0.0.1:8025"
            mail_baseline = {
                m["ID"] for m in request("/api/v1/messages?limit=1000", base=mail_base)["messages"]
            }
            previous = request("/api/v1/alerts?limit=100", token=token)
            previous_ids = {event["id"] for event in previous}

            def new_firing():
                events = request("/api/v1/alerts?status=firing&limit=100", token=token)
                return next(
                    (
                        e
                        for e in events
                        if e["labels"].get("alertname") == "LocalWorkerDown"
                        and e["server_id"] == server_id
                        and e["labels"].get("container_id") == worker["id"]
                        and e["id"] not in previous_ids
                    ),
                    None,
                )

            try:
                compose("stop")
                event = wait_for("real no-HTTP container firing event", new_firing, seconds=240)
                report["fault_event_id"] = event["id"]
                service_token = (ROOT / ".local/alertmanager.token").read_text().strip()
                duplicate = {
                    "version": "4",
                    "alerts": [
                        {
                            "status": "firing",
                            "fingerprint": event["fingerprint"],
                            "startsAt": event["starts_at"],
                            "endsAt": "0001-01-01T00:00:00Z",
                            "labels": event["labels"],
                            "annotations": event["annotations"],
                        }
                    ],
                }
                for _ in range(2):
                    assert (
                        request("/api/v1/hooks/alertmanager", "POST", duplicate, service_token)[
                            "changed"
                        ]
                        == 0
                    )
                report["duplicate_webhook_idempotent"] = True
                request(
                    f"/api/v1/alerts/{event['id']}/ack",
                    "POST",
                    {"comment": "Local acceptance: no-HTTP test-worker intentionally stopped"},
                    token,
                )
                assert (
                    request(f"/api/v1/containers/{worker['id']}", token=token)["status"]
                    == "stopped"
                )
                report["automatic_stopped_status"] = True

                def firing_email():
                    messages = request("/api/v1/messages?limit=1000", base=mail_base)["messages"]
                    return email_for_event(
                        messages,
                        mail_baseline,
                        event,
                        "firing",
                        lambda id: request(f"/api/v1/message/{id}", base=mail_base),
                    )

                report["firing_email"] = wait_for("SMTP firing email", firing_email)
            finally:
                compose("start")

            def recovered():
                current = request(f"/api/v1/alerts/{event['id']}", token=token)
                return current if current["status"] == "resolved" else None

            resolved = wait_for("recovery of the same event", recovered)
            assert resolved["acknowledged_at"] and resolved["ends_at"]
            report["recovery_same_event"] = True
            events = request("/api/v1/alerts?limit=100", token=token)
            assert (
                sum(
                    e["fingerprint"] == event["fingerprint"]
                    and e["starts_at"] == event["starts_at"]
                    for e in events
                )
                == 1
            )

            def resolved_email():
                messages = request("/api/v1/messages?limit=1000", base=mail_base)["messages"]
                return email_for_event(
                    messages,
                    mail_baseline,
                    event,
                    "resolved",
                    lambda id: request(f"/api/v1/message/{id}", base=mail_base),
                )

            report["resolved_email"] = wait_for("SMTP recovery email", resolved_email)
            wait_for(
                "automatic running status",
                lambda: (
                    request(f"/api/v1/containers/{worker['id']}", token=token)["status"]
                    == "running"
                ),
            )
            report["automatic_running_status"] = True
        (ROOT / ".local/batch3-4-acceptance.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(json.dumps(report, ensure_ascii=False, indent=2))
    finally:
        request("/api/v1/auth/logout", "POST", token=token)


if __name__ == "__main__":
    main()
