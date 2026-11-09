"""Real sshd + Docker CLI acceptance; diagnosis Provider remains explicitly simulated."""

import json
import re
import subprocess
from datetime import UTC, datetime
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from uuid import uuid4

import paramiko
from accept_ai import scenario
from accept_monitoring import ROOT, request, wait_for


def compose(*args, test=False):
    files = ["-f", "compose.yaml"]
    if test:
        files += ["-f", "compose.workers-standalone.yaml"]
        files += ["-f", "backend/ai-test-compose.yaml", "-f", "backend/execution-test-compose.yaml"]
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
    result = subprocess.run(
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
            "--profile",
            "execution",
            *args,
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def docker_state(service):
    identifier = compose("ps", "-a", "-q", service, test=True)
    return json.loads(
        subprocess.run(
            ["docker", "inspect", identifier], check=True, capture_output=True, text=True
        ).stdout
    )[0]


def denied(path, token):
    try:
        with urlopen(
            Request(
                "http://127.0.0.1:8000" + path,
                method="POST",
                headers={"Authorization": "Bearer " + token},
            ),
            timeout=15,
        ):
            raise AssertionError("Unapproved execution accepted")
    except HTTPError as exc:
        assert exc.code == 409


def ready():
    try:
        return request("/health/ready")
    except (OSError, RuntimeError):
        return None


def main(after_execution=None, batch=9):
    key = ROOT / ".local/ai-secrets/deepseek.key"
    previous = key.read_bytes() if key.exists() else None
    directory = ROOT / ".local/execution-fixture"
    directory.mkdir(parents=True, exist_ok=True)
    auth_key = paramiko.RSAKey.generate(2048)
    (directory / "authorized_keys").write_text(
        auth_key.get_name() + " " + auth_key.get_base64() + "\n", encoding="utf-8"
    )
    private_path = directory / "client.key"
    auth_key.write_private_key_file(str(private_path))
    report = {
        "checked_at": datetime.now(UTC).isoformat(),
        "status": "running",
        "provider": "local HTTP simulation; not real DeepSeek",
        "ssh": "real OpenSSH sshd and Docker CLI",
    }
    token = server_id = credential_id = rule_id = document_id = None
    cleanup = []
    try:
        key.write_text("batch9-local-dummy-" + uuid4().hex, encoding="utf-8")
        compose(
            "up",
            "-d",
            "--no-build",
            "--wait",
            "backend",
            "ai-worker",
            "execution-worker",
            "test-diagnosis",
            "test-worker-action",
            "test-action-guard",
            "test-ssh",
            test=True,
        )
        print("Local real SSH and HEALTHCHECK fixtures started", flush=True)
        target = docker_state("test-worker-action")
        guard_before = docker_state("test-action-guard")["State"]["StartedAt"]
        (directory / "allowed_ids").write_text(target["Id"] + "\n", encoding="utf-8")
        host_key = compose(
            "exec", "-T", "test-ssh", "cat", "/etc/ssh/ssh_host_ed25519_key.pub", test=True
        )
        credentials = {}
        for line in (
            (ROOT / ".local/admin-credentials.txt").read_text(encoding="utf-8").splitlines()
        ):
            match = re.match(r"(username|password)\s*[:=]\s*(.+)", line)
            if match:
                credentials[match[1]] = match[2]
        token = request("/api/v1/auth/login", "POST", credentials)["access_token"]
        suffix = uuid4().hex[:10]
        credential = request(
            "/api/v1/ssh-credentials",
            "POST",
            {
                "name": "batch9-ssh-" + suffix,
                "auth_type": "private_key",
                "secret": private_path.read_text(),
            },
            token,
        )
        credential_id = credential["id"]
        server = request(
            "/api/v1/servers",
            "POST",
            {
                "name": "batch9-server-" + suffix,
                "host": "test-ssh",
                "ssh_port": 2222,
                "ssh_user": "root",
                "credential_id": credential_id,
                "ssh_host_key": host_key,
                "environment": "local-acceptance",
                "node_exporter_target": "node-exporter:9100",
                "cadvisor_target": "cadvisor:8080",
            },
            token,
        )
        server_id = server["id"]
        assert request(f"/api/v1/servers/{server_id}/test-ssh", "POST", token=token)["success"]

        def inventory():
            try:
                rows = request(f"/api/v1/servers/{server_id}/sync-containers", "POST", token=token)
            except (RuntimeError, OSError):
                # Prometheus must first discover and scrape the newly registered server.
                return None
            return next((row for row in rows if row["name"].endswith("test-worker-action-1")), None)

        container = wait_for("cAdvisor discovers HEALTHCHECK target", inventory, seconds=90)
        document = request(
            "/api/v1/knowledge",
            "POST",
            {
                "title": "Batch9 Docker 故障恢复 SOP " + suffix,
                "content": "容器停止后先核对退出状态和证据，人工审批固定重启动作。"
                "重启后检查 running 和 Docker HEALTHCHECK。",
                "tags": ["batch9"],
            },
            token,
        )
        document_id = document["id"]
        wait_for(
            "knowledge index",
            lambda: (
                request(f"/api/v1/knowledge/{document_id}", token=token)["index_status"] == "ready"
            ),
            seconds=90,
        )
        rule = request(
            "/api/v1/alert-rules",
            "POST",
            {
                "code": "Batch9Stopped_" + suffix,
                "metric": "container_up",
                "threshold": 1,
                "server_id": server_id,
                "for_seconds": 15,
                "severity": "critical",
            },
            token,
        )
        rule_id = rule["id"]
        request("/api/v1/monitoring/apply-rules", "POST", token=token)
        compose("stop", "test-worker-action", test=True)
        print("Real Docker fault started; waiting for monitoring incident", flush=True)

        def firing():
            events = request(
                f"/api/v1/alerts?server_id={server_id}&status=firing&limit=100", token=token
            )
            return next(
                (
                    e
                    for e in events
                    if e["rule_id"] == rule_id
                    and e["labels"].get("container_id") == container["id"]
                    and e["incident_id"]
                ),
                None,
            )

        event = wait_for("Prometheus -> Alertmanager -> incident", firing, seconds=240)
        incident_id = event["incident_id"]
        base = f"/api/v1/incidents/{incident_id}"
        scenario("normal")
        analysis = request(base + "/analyze", "POST", {"force": True}, token)

        def diagnosis():
            row = request(f"/api/v1/analyses/{analysis['id']}", token=token)
            return row if row["status"] in ("completed", "failed", "interrupted") else None

        analysis = wait_for("Harness read-only diagnosis proposal", diagnosis, seconds=90)
        assert analysis["status"] == "completed", analysis["error_code"]
        assert analysis["result"]["proposal"]["parameters"]["container_id"] == container["id"]
        assert analysis["result"]["proposal"]["executable"] is False
        approval = request(base + "/approvals", "POST", {"analysis_id": analysis["id"]}, token)
        denied(f"/api/v1/approvals/{approval['id']}/execute", token)
        assert docker_state("test-worker-action")["State"]["Running"] is False
        report["unapproved_denied"] = True
        rejected = request(
            f"/api/v1/approvals/{approval['id']}/reject",
            "POST",
            {"comment": "先验证拒绝边界"},
            token,
        )
        denied(f"/api/v1/approvals/{rejected['id']}/execute", token)
        report["rejected_denied"] = True
        approval = request(base + "/approvals", "POST", {"analysis_id": analysis["id"]}, token)
        compose("restart", "backend", "execution-worker", test=True)
        wait_for("API ready after pending approval restart", ready, seconds=90)
        assert request(f"/api/v1/approvals/{approval['id']}", token=token)["status"] == "pending"
        report["pending_restart_persistence"] = True
        request(
            f"/api/v1/approvals/{approval['id']}/approve",
            "POST",
            {"comment": "确认仅恢复这个本地测试容器"},
            token,
        )
        execution = request(f"/api/v1/approvals/{approval['id']}/execute", "POST", token=token)
        assert (
            request(f"/api/v1/approvals/{approval['id']}/execute", "POST", token=token)["id"]
            == execution["id"]
        )

        def completed():
            row = request(f"/api/v1/executions/{execution['id']}", token=token)
            return (
                row
                if row["status"] not in ("queued", "prechecking", "executing", "verifying")
                else None
            )

        execution = wait_for(
            "SSH restart and real Docker healthy verification", completed, seconds=120
        )
        assert execution["status"] == "succeeded", execution["error_code"]
        assert execution["verification"]["application_health"] == "healthy"
        assert docker_state("test-action-guard")["State"]["StartedAt"] == guard_before
        target_after = docker_state("test-worker-action")
        started_at = target_after["State"]["StartedAt"]
        assert target_after["State"]["Health"]["Status"] == "healthy"
        wait_for(
            "monitoring independently recovers incident",
            lambda: request(base, token=token)["status"] == "recovered",
            seconds=240,
        )
        compose("restart", "backend", "execution-worker", test=True)
        wait_for("ready after successful execution restart", ready, seconds=90)
        persisted = request(f"/api/v1/executions/{execution['id']}", token=token)
        assert persisted == execution
        assert (
            request(f"/api/v1/approvals/{approval['id']}/execute", "POST", token=token)["id"]
            == execution["id"]
        )
        assert docker_state("test-worker-action")["State"]["StartedAt"] == started_at
        logs = request(f"/api/v1/audit-logs?approval_id={approval['id']}&limit=100", token=token)
        assert {r["action"] for r in logs} >= {
            "approval_requested",
            "approval_approved",
            "execution_queued",
            "execution_prechecking",
            "execution_executing",
            "execution_verifying",
            "execution_succeeded",
        }
        report.update(
            {
                "status": "passed",
                "incident_id": incident_id,
                "analysis_id": analysis["id"],
                "approval": request(f"/api/v1/approvals/{approval['id']}", token=token),
                "execution": execution,
                "audit": logs,
                "guard_unchanged": True,
                "completed_restart_persistence": True,
                "duplicate_requests_reuse_execution": True,
            }
        )
        print("Real SSH restart, HEALTHCHECK, audit and restart persistence passed", flush=True)
        if after_execution:
            after_execution(report, token)
    except Exception as exc:
        report["status"], report["error_type"] = "failed", type(exc).__name__
        raise
    finally:

        def clean(label, action):
            try:
                action()
            except Exception as exc:
                cleanup.append({"step": label, "error_type": type(exc).__name__})

        clean("restore_target", lambda: compose("start", "test-worker-action", test=True))
        if token:
            if rule_id:
                clean(
                    "remove_rule",
                    lambda: request(f"/api/v1/alert-rules/{rule_id}", "DELETE", token=token),
                )
            clean(
                "apply_rules",
                lambda: request("/api/v1/monitoring/apply-rules", "POST", token=token),
            )
            if document_id:
                clean(
                    "remove_document",
                    lambda: request(f"/api/v1/knowledge/{document_id}", "DELETE", token=token),
                )
            if server_id:
                clean(
                    "remove_server",
                    lambda: request(f"/api/v1/servers/{server_id}", "DELETE", token=token),
                )
            if credential_id:
                clean(
                    "remove_credential",
                    lambda: request(
                        f"/api/v1/ssh-credentials/{credential_id}", "DELETE", token=token
                    ),
                )
        if previous is None:
            key.unlink(missing_ok=True)
        else:
            key.write_bytes(previous)
        clean(
            "remove_test_services",
            lambda: compose(
                "rm",
                "-s",
                "-f",
                "test-ssh",
                "test-worker-action",
                "test-action-guard",
                "test-diagnosis",
                test=True,
            ),
        )
        clean(
            "restore_normal_profile",
            lambda: compose(
                "up",
                "-d",
                "--no-build",
                "--wait",
                "backend",
                "ai-worker",
                "prediction-worker",
                "execution-worker",
                *(["report-worker"] if after_execution else []),
            ),
        )
        private_path.unlink(missing_ok=True)
        (directory / "authorized_keys").unlink(missing_ok=True)
        (directory / "allowed_ids").unlink(missing_ok=True)
        report["cleanup"] = {"complete": not cleanup, "errors": cleanup}
        (ROOT / f".local/batch{batch}-acceptance.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        if cleanup:
            raise RuntimeError(f"Acceptance cleanup incomplete; see batch{batch}-acceptance.json")


if __name__ == "__main__":
    main()
