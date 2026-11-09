"""Real Compose acceptance. Synthetic history uses a separate, real Prometheus TSDB.

No synthetic raw series are inserted into the production Prometheus. The two fixture
phases model an overloaded resource and its recovery; both are explicitly simulated.
"""

import json
import os
import re
import subprocess
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from accept_monitoring import ROOT, request, wait_for

MAIL = "http://127.0.0.1:8025"
FIXTURE = "http://127.0.0.1:19090"


def fixture_ready():
    try:
        return request("/api/v1/status/buildinfo", base=FIXTURE)
    except RuntimeError:
        return None


def docker(*args, fixture_dir=None):
    env = os.environ.copy()
    command = ["docker", "compose"]
    if not fixture_dir:
        args = tuple("backend" if value == "prediction-worker" else value for value in args)
    if fixture_dir:
        env["PREDICTION_FIXTURE_DIR"] = str(fixture_dir)
        command += [
            "-f",
            "compose.yaml",
            "-f",
            "compose.workers-standalone.yaml",
            "-f",
            "backend/prediction-test-compose.yaml",
            "--profile",
            "monitoring",
            "--profile",
            "prediction-test",
        ]
    completed = subprocess.run(
        command + list(args), cwd=ROOT, env=env, capture_output=True, text=True, check=True
    )
    return completed.stdout


def fixture_history(directory, server_id, recovering=False):
    directory.mkdir(parents=True)
    assert directory.resolve().is_relative_to((ROOT / ".local").resolve())
    end = int(datetime.now(UTC).timestamp()) // 300 * 300
    start = end - 12 * 86400
    (directory / "prometheus.yml").write_text(
        "global:\n  scrape_interval: 15s\nscrape_configs: []\n"
    )
    labels = f'job="node",server_id="{server_id}",instance="synthetic-resource:9100"'
    times = list(range(start, end + 1, 300))
    if recovering:
        times += list(range(end - 285, end, 15))
        times.sort()
    with (directory / "history.om").open("w", encoding="utf-8", newline="\n") as output:
        for metric in ("node_memory_MemTotal_bytes", "node_memory_MemAvailable_bytes", "up"):
            output.write(f"# TYPE {metric} gauge\n")
            for timestamp in times:
                value = (
                    1000
                    if metric == "node_memory_MemTotal_bytes"
                    else (950 if recovering and timestamp > end - 300 else 700)
                    if metric == "node_memory_MemAvailable_bytes"
                    else 1
                )
                output.write(f"{metric}{{{labels}}} {value} {timestamp}\n")
        output.write("# EOF\n")
    subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--user",
            "0:0",
            "--entrypoint",
            "/bin/promtool",
            "-v",
            f"{directory}:/fixtures",
            "quay.io/prometheus/prometheus:v3.5.0",
            "tsdb",
            "create-blocks-from",
            "openmetrics",
            "--quiet",
            "/fixtures/history.om",
            "/fixtures/data",
        ],
        check=True,
        capture_output=True,
    )
    return end


def result(token, task_id):
    queued = request(f"/api/v1/prediction-tasks/{task_id}/run", "POST", token=token)
    return wait_for(
        "worker forecast completion",
        lambda: (
            value
            if (value := request("/api/v1/predictions/" + queued["id"], token=token))["status"]
            not in ("queued", "running")
            else None
        ),
        180,
    )


def mail_for(baseline, event, status):
    for message in request("/api/v1/messages", base=MAIL)["messages"]:
        if message["ID"] in baseline or status.upper() not in message["Subject"]:
            continue
        if event["labels"]["alertname"] not in message["Subject"]:
            continue
        body = request(f"/api/v1/message/{message['ID']}", base=MAIL)
        content = body.get("HTML", "") + body.get("Text", "")
        if event["prediction_task_id"] in content and "host_memory_percent" in content:
            return {"id": message["ID"], "subject": message["Subject"]}
    return None


def main():
    import sys

    sys.path.insert(0, str(ROOT / "backend"))
    from app.db.session import SessionLocal
    from app.models import Server

    credentials = {}
    for line in (ROOT / ".local/admin-credentials.txt").read_text(encoding="utf-8").splitlines():
        match = re.match(r"(username|password)\s*[:=]\s*(.+)", line)
        if match:
            credentials[match[1]] = match[2]
    token = request("/api/v1/auth/login", "POST", credentials)["access_token"]
    servers = request("/api/v1/servers", token=token)
    live = next(s for s in servers if s["monitoring_enabled"])
    suffix = uuid4().hex[:8]
    report = {
        "checked_at": datetime.now(UTC).isoformat(),
        "long_term_live_accuracy_verified": False,
        "synthetic_history_is_production_data": False,
    }
    live_task = request(
        "/api/v1/prediction-tasks",
        "POST",
        {
            "name": "batch6-live-" + suffix,
            "server_id": live["id"],
            "metric": "host_memory_percent",
            "threshold": 85,
        },
        token,
    )
    real_result = result(token, live_task["id"])
    assert real_result["status"] == "completed", real_result["error_code"]
    report["real_local_metrics"] = {
        "task_id": live_task["id"],
        "result_id": real_result["id"],
        "quality": real_result["quality"],
        "horizons": {k: v["quality"] for k, v in real_result["horizons"].items()},
    }
    synthetic = request(
        "/api/v1/servers",
        "POST",
        {
            "name": "batch6-synthetic-" + suffix,
            "host": "127.0.0.1",
            "node_exporter_target": "synthetic-resource:9100",
            "cadvisor_target": "synthetic-resource:8080",
            "environment": "synthetic-batch6-acceptance",
        },
        token,
    )
    # This asset represents a deliberately backdated test history, never a real server.
    with SessionLocal() as db:
        asset = db.get(Server, UUID(synthetic["id"]))
        asset.created_at = datetime.now(UTC) - timedelta(days=13)
        db.commit()
    body = {
        "name": "batch6-synthetic-" + suffix,
        "server_id": synthetic["id"],
        "metric": "host_memory_percent",
        "threshold": 10,
        "enabled": True,
        "interval_seconds": 86400,
    }
    task = request("/api/v1/prediction-tasks", "POST", {**body, "enabled": False}, token)
    root = ROOT / ".local" / ("prediction-fixture-" + suffix)
    phase = root / "risk"
    rule_id = None
    try:
        docker("stop", "prediction-worker")
        fixture_history(phase, synthetic["id"])
        docker("up", "-d", "prediction-fixture", "prediction-fixture-worker", fixture_dir=phase)
        wait_for(
            "isolated Prometheus ready",
            fixture_ready,
            60,
        )
        request("/api/v1/prediction-tasks/" + task["id"], "PUT", body, token)

        # The enabled task may already have been automatically scheduled.
        def scheduled():
            runs = request("/api/v1/predictions?task_id=" + task["id"], token=token)
            return next((run for run in runs if run["status"] == "completed"), None)

        risk_result = wait_for("scheduled fixture forecast", scheduled, 180)
        assert all(h["quality"] == "validated" for h in risk_result["horizons"].values())
        assert risk_result["horizons"]["1"]["risk"]["level"] == "expected"
        baseline = {m["ID"] for m in request("/api/v1/messages", base=MAIL)["messages"]}
        rule = request(
            "/api/v1/alert-rules",
            "POST",
            {
                "code": "Batch6Forecast_" + suffix,
                "prediction_task_id": task["id"],
                "prediction_horizon_hours": 1,
                "metric": "prediction_risk",
                "threshold": 1,
                "for_seconds": 0,
            },
            token,
        )
        rule_id = rule["id"]
        request("/api/v1/monitoring/apply-rules", "POST", token=token)
        firing = wait_for(
            "forecast alert event",
            lambda: next(
                (
                    e
                    for e in request("/api/v1/alerts?prediction_task_id=" + task["id"], token=token)
                    if e["status"] == "firing"
                ),
                None,
            ),
            90,
        )
        firing_mail = wait_for(
            "forecast firing email", lambda: mail_for(baseline, firing, "firing"), 90
        )
        service_token = (ROOT / ".local/alertmanager.token").read_text().strip()
        payload = {
            "alerts": [
                {
                    "fingerprint": firing["fingerprint"],
                    "status": "firing",
                    "startsAt": firing["starts_at"],
                    "endsAt": "0001-01-01T00:00:00Z",
                    "labels": firing["labels"],
                    "annotations": firing["annotations"],
                }
            ]
        }
        duplicate = request("/api/v1/hooks/alertmanager", "POST", payload, service_token)
        assert duplicate["changed"] == 0
        print(
            "Synthetic risk forecast, Prometheus alert, webhook and firing email verified",
            flush=True,
        )
        report["synthetic_risk"] = {
            "task_id": task["id"],
            "result_id": risk_result["id"],
            "event_id": firing["id"],
            "mail": firing_mail,
            "duplicate_changed": duplicate["changed"],
            "models": {h: v["model"] for h, v in risk_result["horizons"].items()},
        }
        docker("stop", "prediction-fixture-worker", "prediction-fixture", fixture_dir=phase)
        phase = root / "recovery"
        fixture_history(phase, synthetic["id"], recovering=True)
        docker(
            "up",
            "-d",
            "--force-recreate",
            "prediction-fixture",
            "prediction-fixture-worker",
            fixture_dir=phase,
        )
        wait_for("recovery fixture ready", fixture_ready, 60)
        recovery = result(token, task["id"])
        assert recovery["status"] == "completed", recovery["error_code"]
        assert recovery["horizons"]["1"]["risk"]["level"] == "normal"
        restored = wait_for(
            "forecast recovery event",
            lambda: (
                e
                if (e := request("/api/v1/alerts/" + firing["id"], token=token))["status"]
                == "resolved"
                else None
            ),
            90,
        )
        resolved_mail = wait_for(
            "forecast recovery email", lambda: mail_for(baseline, firing, "resolved"), 90
        )
        report["synthetic_recovery"] = {
            "result_id": recovery["id"],
            "event_id": restored["id"],
            "mail": resolved_mail,
        }
        # Restart must preserve persisted results and detection state.
        docker("restart", "prediction-fixture-worker", fixture_dir=phase)
        assert (
            request("/api/v1/predictions/" + recovery["id"], token=token)["status"] == "completed"
        )
        report["restart_persistence"] = True
        report["api_health"] = request("/health/ready")
    finally:
        if rule_id:
            request("/api/v1/alert-rules/" + rule_id, "DELETE", token=token)
        request("/api/v1/prediction-tasks/" + task["id"], "PUT", {**body, "enabled": False}, token)
        request("/api/v1/monitoring/apply-rules", "POST", token=token)
        docker("stop", "prediction-fixture-worker", "prediction-fixture", fixture_dir=phase)
        docker("up", "-d", "prediction-worker")
        request("/api/v1/servers/" + synthetic["id"], "DELETE", token=token)
        (ROOT / ".local/batch6-acceptance.json").write_text(
            json.dumps(report, indent=2), encoding="utf-8"
        )
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
