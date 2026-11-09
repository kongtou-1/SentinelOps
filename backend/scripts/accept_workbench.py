"""Real SDK conversation -> adopted diagnosis -> local SSH recovery -> Mailpit.

Runs in a fresh database. --browser pauses at an open incident for UI checks.
Normal services, rules and secrets are restored even if acceptance fails.
"""

import json
import os
import re
import secrets
import subprocess
import sys
import threading
import time
from pathlib import Path
from urllib.request import Request, urlopen
from uuid import uuid4

import accept_executions
import accept_reports
from accept_ai import scenario
from accept_monitoring import request, wait_for
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session

ROOT = Path(__file__).resolve().parents[2]


def main():
    sys.path.insert(0, str(ROOT / "backend"))
    from app.core.config import get_settings
    from app.models import Role, User

    settings = get_settings()
    database = "ops_workbench_" + uuid4().hex[:8] + "_test"
    credentials = dict(
        re.findall(
            r"(username|password)\s*[:=]\s*(.+)",
            (ROOT / ".local/admin-credentials.txt").read_text(encoding="utf-8"),
        )
    )
    source = create_engine(settings.database_url)
    with Session(source) as db:
        admin = db.scalar(select(User).where(User.username == credentials["username"]))
        identifier, hashed = admin.id, admin.password_hash
    source.dispose()
    connection = create_engine(
        settings.database_url.set(database="postgres"), isolation_level="AUTOCOMMIT"
    )
    with connection.connect() as db:
        db.execute(text('CREATE DATABASE "' + database + '"'))
    connection.dispose()
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=ROOT / "backend",
        env={**os.environ, "POSTGRES_DB": database},
        check=True,
        capture_output=True,
    )
    target = create_engine(settings.database_url.set(database=database))
    with Session(target) as db:
        role_id = db.scalar(select(Role.id).where(Role.code == "admin"))
        db.add(
            User(
                id=identifier,
                username=credentials["username"],
                password_hash=hashed,
                role_id=role_id,
            )
        )
        db.commit()
    target.dispose()
    override = ROOT / ".local/workbench-acceptance-compose.yaml"
    override.write_text(
        "services:\n"
        + "".join(
            f"  {service}:\n    environment:\n      POSTGRES_DB: {database}\n"
            for service in (
                "backend",
                "ai-worker",
                "prediction-worker",
                "execution-worker",
                "report-worker",
            )
        ),
        encoding="utf-8",
    )
    original_compose = accept_executions.compose
    original_request = accept_executions.request
    checks = {"isolated_database": database}
    marker = ROOT / ".local/workbench-browser.done"
    marker.unlink(missing_ok=True)

    def compose(*args, test=False):
        if not test:
            return original_compose(*args)
        result = subprocess.run(
            [
                "docker",
                "compose",
                "-f",
                "compose.yaml",
                "-f",
                "compose.workers-standalone.yaml",
                "-f",
                "backend/ai-test-compose.yaml",
                "-f",
                "backend/execution-test-compose.yaml",
                "-f",
                str(override),
                "--profile",
                "monitoring",
                "--profile",
                "local-test",
                "--profile",
                "ai",
                "--profile",
                "execution",
                "--profile",
                "reports",
                *args,
            ],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        return result.stdout.strip()

    def send(conversation_id, token, content):
        path = f"/api/v1/analysis-conversations/{conversation_id}/messages"
        body = {"client_message_id": str(uuid4()), "content": content}
        row = request(path, "POST", body, token)
        assert request(path, "POST", body, token)["id"] == row["id"]
        return row

    def result(run_id, token):
        def terminal():
            row = request(f"/api/v1/analysis-runs/{run_id}", token=token)
            return row if row["status"] not in ("queued", "running", "cancelling") else None

        return wait_for("conversation terminal", terminal, seconds=90)

    def live_diagnosis(path, method="GET", body=None, token=None):
        if method != "POST" or not path.endswith("/analyze"):
            return original_request(path, method, body, token)
        base = path.removesuffix("/analyze")
        scenario("chat_slow")
        conversation = request(
            base + "/analysis-conversations", "POST", {"title": "运行过程与补充验收"}, token
        )
        cid = conversation["id"]
        stream_events, stream_errors = [], []
        stop = threading.Event()

        def subscribe():
            try:
                with urlopen(
                    Request(
                        "http://127.0.0.1:8080/api/v1/analysis-conversations/" + cid + "/stream",
                        headers={"Authorization": "Bearer " + token},
                    ),
                    timeout=20,
                ) as response:
                    for line in response:
                        if line.startswith(b"data: "):
                            stream_events.append(json.loads(line[6:]))
                        if stop.is_set():
                            break
            except Exception as exc:
                stream_errors.append(type(exc).__name__)

        subscriber = threading.Thread(target=subscribe, daemon=True)
        subscriber.start()
        first = send(cid, token, "请读取工具证据，诊断当前容器故障")
        wait_for(
            "live model event through Nginx",
            lambda: any(e["kind"] == "model_started" for e in stream_events),
            seconds=45,
        )
        assert (
            request(f"/api/v1/analysis-runs/{first['run_id']}", token=token)["status"] == "running"
        )
        supplement = send(cid, token, "补充：这次故障前已经发生两次进程退出。")
        assert supplement["run_id"] == first["run_id"]
        run = result(first["run_id"], token)
        stop.set()
        subscriber.join(12)
        assert not stream_errors, stream_errors
        assert run["status"] == "completed", run["error_code"]
        messages = request(f"/api/v1/analysis-conversations/{cid}/messages", token=token)
        assert next(m for m in messages if m["id"] == supplement["id"])["status"] == "accepted"
        assert any("两次进程退出" in m["content"] for m in messages if m["role"] == "assistant")
        events = request(f"/api/v1/analysis-conversations/{cid}/events?limit=1000", token=token)
        assert [e["seq"] for e in events] == list(range(1, len(events) + 1))
        kinds = {e["kind"] for e in events}
        assert kinds >= {
            "model_started",
            "tool_started",
            "tool_finished",
            "messages_accepted",
            "assistant_delta",
            "run_completed",
        }
        replay = request(
            f"/api/v1/analysis-conversations/{cid}/events?after_seq=5&limit=1000", token=token
        )
        assert replay == events[5:]
        adopted = request(f"/api/v1/analysis-runs/{run['id']}/adopt", "POST", token=token)
        assert (
            request(f"/api/v1/analysis-runs/{run['id']}/adopt", "POST", token=token)["id"]
            == adopted["id"]
        )
        checks.update(
            conversation_id=cid,
            run_id=run["id"],
            live_sse_before_completion=True,
            supplement_accepted=True,
            event_count=len(events),
            model_requests=run["model_requests"],
            tool_count=run["tool_count"],
            duplicate_message_and_adoption_reused=True,
            cursor_replay_complete=True,
        )
        if "--browser" in sys.argv:
            accounts = []
            for username, role in (
                ("workbench_operator", "operator"),
                ("workbench_operator2", "operator"),
                ("workbench_viewer", "viewer"),
            ):
                account = {
                    "username": username,
                    "password": secrets.token_urlsafe(20),
                    "role": role,
                }
                request("/api/v1/users", "POST", account, token)
                accounts.append(account)
            (ROOT / ".local/workbench-browser-credentials.json").write_text(
                json.dumps([{"role": "admin", **credentials}, *accounts]), encoding="utf-8"
            )
            op_token = request(
                "/api/v1/auth/login", "POST", {k: accounts[0][k] for k in ("username", "password")}
            )["access_token"]
            other = request(
                base + "/analysis-conversations", "POST", {"title": "Operator 私有会话"}, op_token
            )
            other_message = send(other["id"], op_token, "请说明当前故障的证据与恢复条件")
            assert result(other_message["run_id"], op_token)["status"] == "completed"
            (ROOT / ".local/workbench-browser-workflow.json").write_text(
                json.dumps(
                    {
                        "database": database,
                        "incident_id": conversation["incident_id"],
                        "conversation_id": cid,
                        "other_conversation_id": other["id"],
                        "run_id": run["id"],
                    }
                ),
                encoding="utf-8",
            )
            print("Workbench browser fixture ready; awaiting local verification marker", flush=True)
            deadline = time.monotonic() + 900
            while not marker.exists() and time.monotonic() < deadline:
                time.sleep(2)
            if not marker.exists():
                raise RuntimeError("Browser verification marker timed out")
            checks["browser"] = json.loads(marker.read_text(encoding="utf-8"))
        # The owned SDK process is killed by an actual worker restart; history remains.
        restart_conversation = request(
            base + "/analysis-conversations", "POST", {"title": "worker 重启与明确继续"}, token
        )
        restarted = send(restart_conversation["id"], token, "开始诊断")
        wait_for(
            "worker running before restart",
            lambda: (
                request(f"/api/v1/analysis-runs/{restarted['run_id']}", token=token)["status"]
                == "running"
            ),
            seconds=30,
        )
        compose("kill", "-s", "SIGKILL", "ai-worker", test=True)
        compose("up", "-d", "--no-build", "--wait", "ai-worker", test=True)
        assert result(restarted["run_id"], token)["status"] == "interrupted"
        continued = send(restart_conversation["id"], token, "明确继续诊断")
        assert continued["run_id"] != restarted["run_id"]
        assert result(continued["run_id"], token)["status"] == "completed"
        assert request(f"/api/v1/analyses/{adopted['id']}", token=token) == adopted
        checks["worker_restart_history_and_explicit_continue"] = True
        scenario("normal")
        print("Live tools, supplement, adoption, replay and worker restart passed", flush=True)
        return adopted

    def reports(evidence, token):
        accept_reports.report_steps(evidence, token)
        evidence["workbench"] = checks

    accept_executions.compose = accept_reports.compose = compose
    accept_executions.request = live_diagnosis
    print("Fresh workbench acceptance database: " + database, flush=True)
    try:
        compose("up", "-d", "--no-build", "--wait", "prediction-worker", "frontend", test=True)
        accept_executions.main(after_execution=reports, batch=12)
    finally:
        original_compose(
            "up",
            "-d",
            "--no-build",
            "--wait",
            "backend",
            "ai-worker",
            "prediction-worker",
            "execution-worker",
            "report-worker",
            "frontend",
        )
        token = request("/api/v1/auth/login", "POST", credentials)["access_token"]
        applied = request("/api/v1/monitoring/apply-rules", "POST", token=token)
        artifact = ROOT / ".local/batch12-acceptance.json"
        if artifact.exists():
            evidence = json.loads(artifact.read_text(encoding="utf-8"))
            evidence.update(normal_services_restored=True, normal_rules_applied=applied["applied"])
            artifact.write_text(
                json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8"
            )
    print("Workbench operations loop passed; normal services restored", flush=True)


if __name__ == "__main__":
    main()
