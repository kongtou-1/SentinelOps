"""Real local Docker fault + real Harness/MCP/vectors + explicitly scripted Provider."""

import json
import re
import subprocess
from datetime import UTC, datetime
from urllib.request import Request, urlopen
from uuid import uuid4

from accept_monitoring import ROOT, request, wait_for


def compose(*args, test=False):
    files = ["-f", "compose.yaml"]
    if test:
        files += ["-f", "compose.workers-standalone.yaml"]
        files += ["-f", "backend/ai-test-compose.yaml"]
    if not test:
        consumers = {
            "operations-worker",
            "prediction-worker",
            "source-worker",
            "inspection-worker",
            "ai-worker",
            "report-worker",
        }
        args = tuple("backend" if value in consumers else value for value in args)
    subprocess.run(
        [
            "docker",
            "compose",
            *files,
            "--profile",
            "monitoring",
            "--profile",
            "local-test",
            "--profile",
            "ai",
            *args,
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )


def scenario(value):
    with urlopen(
        Request(
            "http://127.0.0.1:18003/scenario",
            data=json.dumps({"scenario": value}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        ),
        timeout=10,
    ) as reply:
        return json.load(reply)


def main():
    key = ROOT / ".local/ai-secrets/deepseek.key"
    key.parent.mkdir(parents=True, exist_ok=True)
    previous_key = key.read_bytes() if key.exists() else None
    report = {
        "checked_at": datetime.now(UTC).isoformat(),
        "status": "running",
        "provider": "scripted local HTTP mock; not real DeepSeek",
        "cleanup": {},
    }
    rules, documents = [], []
    server_id, token = None, None
    cleanup_errors = []
    try:
        key.write_text("local-acceptance-dummy-key-" + uuid4().hex, encoding="utf-8")
        compose(
            "up", "-d", "--no-build", "--wait", "backend", "ai-worker", "test-diagnosis", test=True
        )
        print("AI test profile started; real Harness and pinned embedding model", flush=True)
        credentials = {}
        for line in (
            (ROOT / ".local/admin-credentials.txt").read_text(encoding="utf-8").splitlines()
        ):
            match = re.match(r"(username|password)\s*[:=]\s*(.+)", line)
            if match:
                credentials[match[1]] = match[2]
        token = request("/api/v1/auth/login", "POST", credentials)["access_token"]
        suffix = uuid4().hex[:10]
        document = request(
            "/api/v1/knowledge",
            "POST",
            {
                "title": f"Docker 容器停止故障 SOP {suffix}",
                "content": "结合 container_up 告警、容器退出状态和宿主机内存指标，"
                "检查容器不可用原因。"
                "先区分采集器失联和进程停止，再由人工审批决定是否重启；恢复后再次检查。",
                "source": "Batch8 local acceptance fixture",
                "tags": ["batch8-acceptance"],
            },
            token,
        )
        documents.append(document["id"])
        wait_for(
            "real Chinese vector indexing",
            lambda: (
                row
                if (row := request(f"/api/v1/knowledge/{document['id']}", token=token))[
                    "index_status"
                ]
                == "ready"
                else None
            ),
            seconds=90,
        )
        results = request(
            "/api/v1/knowledge/search?q=Docker%20%E5%AE%B9%E5%99%A8%E5%81%9C%E6%AD%A2&tag=batch8-acceptance",
            token=token,
        )
        assert results and results[0]["document_id"] == document["id"]
        report["retrieval"] = results
        server = request(
            "/api/v1/servers",
            "POST",
            {
                "name": "batch8-local-" + suffix,
                "host": "127.0.0.1",
                "environment": "batch8-local-acceptance",
                "node_exporter_target": "node-exporter:9100",
                "cadvisor_target": "cadvisor:8080",
            },
            token,
        )
        server_id = server["id"]

        def inventory():
            try:
                return request(f"/api/v1/servers/{server_id}/sync-containers", "POST", token=token)
            except RuntimeError:
                return None

        containers = wait_for("local collector inventory", inventory)
        container = next(
            c
            for c in containers
            if c["name"] == "sentinelops-test-worker-1" and c["status"] == "running"
        )
        report["server_id"], report["container_id"] = server_id, container["id"]
        rule = request(
            "/api/v1/alert-rules",
            "POST",
            {
                "code": "Batch8WorkerDown_" + suffix,
                "metric": "container_up",
                "threshold": 1,
                "server_id": server_id,
                "for_seconds": 15,
                "severity": "critical",
            },
            token,
        )
        rules.append(rule["id"])
        request("/api/v1/monitoring/apply-rules", "POST", token=token)
        fault_at = datetime.now(UTC)
        compose("stop", "test-worker")

        def firing():
            events = request(
                f"/api/v1/alerts?server_id={server_id}&status=firing&limit=100", token=token
            )
            return next(
                (
                    e
                    for e in events
                    if e["rule_id"] == rule["id"]
                    and e["labels"].get("container_id") == container["id"]
                    and e["incident_id"]
                    and datetime.fromisoformat(e["starts_at"].replace("Z", "+00:00")) >= fault_at
                ),
                None,
            )

        event = wait_for(
            "real container fault -> Prometheus -> Alertmanager -> incident", firing, seconds=240
        )
        report["alert"] = event
        incident_id = event["incident_id"]
        report["incident_id"] = incident_id
        base = f"/api/v1/incidents/{incident_id}"
        print("Real container outage created an incident; analyzing it", flush=True)

        def analyze():
            row = request(base + "/analyze", "POST", {"force": True}, token)

            def finished():
                current = request(f"/api/v1/analyses/{row['id']}", token=token)
                return (
                    current if current["status"] in ("completed", "failed", "interrupted") else None
                )

            return wait_for("analysis terminal result", finished, seconds=90)

        scenario("normal")
        diagnosed = analyze()
        assert diagnosed["status"] == "completed", diagnosed["error_code"]
        assert diagnosed["knowledge"] and diagnosed["result"]["knowledge_ids"]
        proposal = diagnosed["result"]["proposal"]
        assert proposal["parameters"]["container_id"] == container["id"]
        assert proposal["risk"] == "medium" and proposal["executable"] is False
        assert proposal["requires_human_approval"] is True
        metric_evidence = [e for e in diagnosed["evidence"] if e["source"] == "get_server_metrics"]
        assert metric_evidence and metric_evidence[0]["data"]["series"]["result"]
        assert diagnosed["tool_calls"][-1]["runtime_tools"] and not any(
            "bash" in t or "execute" in t for t in diagnosed["tool_calls"][-1]["runtime_tools"]
        )
        report["fault_diagnosis"] = diagnosed
        print("Fault diagnosis completed with real metric and knowledge evidence", flush=True)
        errors = {}
        for value, expected in (
            ("401", "authentication_failed"),
            ("402", "insufficient_balance"),
            ("429", "rate_limited"),
            ("500", "service_error"),
            ("503", "service_error"),
            ("malicious", "tool_not_allowed"),
            ("outside_asset", "asset_not_allowed"),
            ("invalid_json", "invalid_response"),
            ("fake_evidence", "invalid_response"),
            ("timeout", "analysis_timeout"),
        ):
            scenario(value)
            result = analyze()
            assert result["status"] == "failed" and result["error_code"] == expected, (
                value,
                result["error_code"],
            )
            errors[value] = {
                "id": result["id"],
                "error_code": result["error_code"],
                "tool_calls": result["tool_calls"],
            }
        report["simulated_provider_failures"] = errors
        print(
            "Provider failures, forged evidence, unauthorized tools and timeout rejected",
            flush=True,
        )
        scenario("timeout")
        interrupted = request(base + "/analyze", "POST", {"force": True}, token)
        wait_for(
            "analysis running before worker restart",
            lambda: (
                r
                if (r := request(f"/api/v1/analyses/{interrupted['id']}", token=token))["status"]
                == "running"
                else None
            ),
        )
        compose("restart", "ai-worker", test=True)
        after = wait_for(
            "interrupted run persisted",
            lambda: (
                r
                if (r := request(f"/api/v1/analyses/{interrupted['id']}", token=token))["status"]
                == "interrupted"
                else None
            ),
            seconds=60,
        )
        report["worker_restart"] = {
            "id": after["id"],
            "status": after["status"],
            "error_code": after["error_code"],
        }
        scenario("normal")
        compose("start", "test-worker")
        wait_for(
            "incident recovery",
            lambda: r if (r := request(base, token=token))["status"] == "recovered" else None,
            seconds=240,
        )
        recovered = analyze()
        assert recovered["status"] == "completed" and recovered["result"]["proposal"] is None
        report["recovery_diagnosis"] = recovered
        before = request(base + "/analyses?limit=100", token=token)
        compose("restart", "backend", test=True)

        def ready():
            try:
                return request("/health/ready")
            except (RuntimeError, OSError):
                return None

        wait_for("backend ready after restart", ready, seconds=90)
        assert request(base + "/analyses?limit=100", token=token) == before
        report["api_restart_persistence"] = True
        report["status"] = "passed"
        print("Recovery analysis and API/worker restart persistence passed", flush=True)
    except Exception as exc:
        report["status"], report["error_type"] = "failed", type(exc).__name__
        raise
    finally:
        for label, action in [
            ("restore_test_worker", lambda: compose("start", "test-worker")),
            ("restore_provider", lambda: scenario("normal")),
        ]:
            try:
                action()
            except Exception as exc:
                cleanup_errors.append({"step": label, "error_type": type(exc).__name__})
        if token:
            for identifier in rules:
                try:
                    request(f"/api/v1/alert-rules/{identifier}", "DELETE", token=token)
                except Exception as exc:
                    cleanup_errors.append({"step": "remove_rule", "error_type": type(exc).__name__})
            try:
                request("/api/v1/monitoring/apply-rules", "POST", token=token)
                for identifier in documents:
                    request(f"/api/v1/knowledge/{identifier}", "DELETE", token=token)
                if server_id:
                    request(f"/api/v1/servers/{server_id}", "DELETE", token=token)
            except Exception as exc:
                cleanup_errors.append({"step": "remove_fixtures", "error_type": type(exc).__name__})
        if previous_key is None:
            key.unlink(missing_ok=True)
        else:
            key.write_bytes(previous_key)
        try:
            compose("stop", "test-diagnosis", test=True)
            compose("rm", "-f", "test-diagnosis", test=True)
            compose("up", "-d", "--no-build", "--wait", "backend", "ai-worker", "prediction-worker")
        except Exception as exc:
            cleanup_errors.append(
                {"step": "restore_normal_profile", "error_type": type(exc).__name__}
            )
        report["cleanup"] = {"complete": not cleanup_errors, "errors": cleanup_errors}
        (ROOT / ".local/batch8-acceptance.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        if cleanup_errors:
            raise RuntimeError("Acceptance cleanup incomplete; see batch8-acceptance.json")


if __name__ == "__main__":
    main()
