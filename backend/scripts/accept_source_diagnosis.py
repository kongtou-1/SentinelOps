"""Isolated source -> real Harness -> adopted evidence and restart acceptance."""

import argparse
import json
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

from accept_sources import request
from sqlalchemy import select

ROOT = Path(__file__).resolve().parents[2]
PASSWORD = "Source-diagnosis-acceptance-2026"


def wait(read, done):
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        value = read()
        if done(value):
            return value
        time.sleep(0.5)
    raise AssertionError("Acceptance operation timed out")


def main():
    sys.path.insert(0, str(ROOT / "backend"))
    from app.core.config import get_settings
    from app.core.security import password_hasher
    from app.db.session import SessionLocal
    from app.models import AlertEvent, Container, Incident, IncidentAlert, Role, User
    from app.services.reports.logic import snapshot as report_snapshot

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    if get_settings().postgres_db != "ops_platform_source_diagnosis_test":
        raise SystemExit("Only the dedicated source diagnosis test database is allowed")
    base = "http://127.0.0.1:18014"
    output = ROOT / ".local/source-diagnosis-acceptance.json"
    with SessionLocal() as db:
        for role in ("admin", "operator", "viewer"):
            for name in (
                ["source2-operator", "source2-other"] if role == "operator" else ["source2-" + role]
            ):
                if not db.scalar(select(User.id).where(User.username == name)):
                    db.add(
                        User(
                            username=name,
                            role_id=db.scalar(select(Role.id).where(Role.code == role)),
                            password_hash=password_hasher.hash(PASSWORD),
                        )
                    )
        db.commit()
    tokens = {}
    for name in ("admin", "operator", "viewer", "other"):
        code, value = request(
            base,
            "/api/v1/auth/login",
            method="POST",
            body={"username": "source2-" + name, "password": PASSWORD},
        )
        assert code == 200
        tokens[name] = value["access_token"]

    def api(path, method="GET", body=None, role="admin", expected=200):
        code, value = request(base, "/api/v1" + path, tokens[role], method, body)
        assert code == expected, (path, code, value)
        return value

    request(
        "http://127.0.0.1:18033", "/scenario", method="POST", body={"scenario": "source_diagnosis"}
    )

    if args.verify:
        saved = json.loads(output.read_text(encoding="utf-8"))
        analysis = api("/analyses/" + saved["analysis_id"], role="viewer")
        assert analysis["source_context"] == saved["source_context"]
        evidence = next(e for e in analysis["evidence"] if e["source"] == "read_source_file")
        assert evidence == saved["file_evidence"]
        file = api(f"/source-snapshots/{saved['snapshot_id']}/file?path=backend/app/main.py")
        assert "timeout = 20" in file["content"]
        message = api(
            f"/analysis-conversations/{saved['conversation_id']}/messages",
            "POST",
            {"client_message_id": str(uuid4()), "content": "重启后复核冻结源码"},
            role="operator",
            expected=202,
        )
        run = wait(
            lambda: api("/analysis-runs/" + message["run_id"], role="operator"),
            lambda r: r["status"] not in ("queued", "running", "cancelling"),
        )
        assert run["status"] == "completed", run
        assert any(c["tool"] == "read_source_file" for c in run["tool_calls"])
        assert (
            "timeout = 20"
            in next(e for e in run["evidence"] if e["source"] == "read_source_file")["data"][
                "content"
            ]
        )
        saved["restart_and_worker_stop_verified"] = True
        output.write_text(json.dumps(saved, ensure_ascii=False, indent=2), encoding="utf-8")
        print(
            "Frozen context and source evidence survived restart; "
            "stopped source worker did not block diagnosis"
        )
        return

    app = api(
        "/applications",
        "POST",
        {
            "code": "source2-backend",
            "name": "源码诊断验收服务",
            "local_path": ".",
            "entrypoint": "app.main:app",
            "path_mappings": [{"runtime_prefix": "/app/app", "repository_prefix": "backend/app"}],
        },
        expected=201,
    )
    server = api(
        "/servers",
        "POST",
        {
            "name": "source2-local-docker",
            "host": "localhost",
            "node_exporter_target": "node-exporter:9100",
            "cadvisor_target": "cadvisor:8080",
        },
        expected=201,
    )
    identity = json.loads(
        subprocess.run(
            ["docker", "inspect", "sentinelops-sources2-backend-1"],
            capture_output=True,
            check=True,
            text=True,
        ).stdout
    )[0]
    with SessionLocal() as db:
        container = Container(
            server_id=UUID(server["id"]),
            docker_id="/docker/" + identity["Id"],
            name="source2-test-worker",
            image=identity["Config"]["Image"],
            status="running",
            labels={},
            last_seen_at=datetime.now(UTC),
            image_id=identity["Image"],
            identity_observed_at=datetime.now(UTC),
            identity={"compose_project": "sentinelops-sources2", "compose_service": "backend"},
        )
        db.add(container)
        db.commit()
        container_id = str(container.id)

    def sync():
        pending = api(
            f"/applications/{app['id']}/source-syncs", "POST", {}, role="operator", expected=202
        )
        ready = wait(
            lambda: api("/source-snapshots/" + pending["id"]),
            lambda s: s["status"] not in ("queued", "running"),
        )
        assert ready["status"] in ("ready", "partial"), ready
        return ready

    fixture = ROOT / ".local/source-diagnosis-fixture/backend/app/main.py"
    fixture.write_text("timeout = 10\nprint(timeout)\n", encoding="utf-8")
    first = sync()
    release1 = api(
        f"/applications/{app['id']}/releases",
        "POST",
        {
            "version": "v1",
            "snapshot_id": first["id"],
            "image_id": identity["Image"],
            "change_summary": "初始 timeout=10",
        },
        expected=201,
    )
    past = datetime.now(UTC) - timedelta(minutes=5)
    boundary = datetime.now(UTC) - timedelta(minutes=1)
    api(
        f"/applications/{app['id']}/deployments",
        "POST",
        {
            "release_id": release1["id"],
            "container_id": container_id,
            "environment": "test",
            "started_at": past.isoformat(),
            "ended_at": boundary.isoformat(),
        },
        expected=201,
    )
    fixture.write_text("timeout = 20\nprint(timeout)\n", encoding="utf-8")
    second = sync()
    release2 = api(
        f"/applications/{app['id']}/releases",
        "POST",
        {
            "version": "v2",
            "snapshot_id": second["id"],
            "image_id": identity["Image"],
            "change_summary": "timeout 改为20",
            "test_result": "本地验收 fixture",
        },
        expected=201,
    )
    api(
        f"/applications/{app['id']}/deployments",
        "POST",
        {
            "release_id": release2["id"],
            "container_id": container_id,
            "environment": "test",
            "started_at": datetime.now(UTC).isoformat(),
        },
        expected=201,
    )
    from app.core.secrets import file_secret

    alert = {
        "status": "firing",
        "fingerprint": uuid4().hex,
        "startsAt": datetime.now(UTC).isoformat(),
        "endsAt": "0001-01-01T00:00:00Z",
        "labels": {
            "server_id": server["id"],
            "id": "/docker/" + identity["Id"],
            "severity": "warning",
            "alertname": "SourceDiagnosisFixture",
        },
        "annotations": {"summary": "源码诊断本地模拟故障"},
    }
    code, _ = request(
        base,
        "/api/v1/hooks/alertmanager",
        file_secret(get_settings().alertmanager_token_file),
        "POST",
        {"alerts": [alert]},
    )
    assert code == 200
    incident = api("/incidents")[0]
    conversation = api(
        f"/incidents/{incident['id']}/analysis-conversations",
        "POST",
        {},
        role="operator",
        expected=201,
    )
    message = api(
        f"/analysis-conversations/{conversation['id']}/messages",
        "POST",
        {
            "client_message_id": str(uuid4()),
            "content": "请读取对应版本源码、文档及差异，生成候选诊断",
        },
        role="operator",
        expected=202,
    )
    run = wait(
        lambda: api("/analysis-runs/" + message["run_id"], role="operator"),
        lambda r: r["status"] not in ("queued", "running", "cancelling"),
    )
    assert run["status"] == "completed", run
    assert {e["source"] for e in run["evidence"]} >= {
        "read_source_file",
        "search_source",
        "compare_source_snapshots",
    }
    analysis = api(f"/analysis-runs/{run['id']}/adopt", "POST", role="operator")
    evidence = next(e for e in analysis["evidence"] if e["source"] == "read_source_file")
    assert "timeout = 20" in evidence["data"]["content"]
    assert "local_source_version_unverified" in analysis["result"]["data_gaps"]
    api(f"/analyses/{analysis['id']}", role="viewer")
    for role in ("viewer", "other"):
        api(f"/analysis-runs/{run['id']}", role=role, expected=403)
    api(f"/analysis-runs/{run['id']}")
    with SessionLocal() as db:
        frozen = report_snapshot(db, db.get(Incident, UUID(incident["id"])))
        projected = next(r for r in frozen["records"] if r["kind"] == "analysis")
        assert projected["data"]["source_context"] == analysis["source_context"]
        assert projected["data"]["evidence"] == analysis["evidence"]
        now = datetime.now(UTC)
        fallback = Incident(
            title="没有源码的模拟故障",
            severity="warning",
            started_at=now,
            last_alert_started_at=now,
        )
        db.add(fallback)
        db.flush()
        event = AlertEvent(
            fingerprint=uuid4().hex,
            server_id=UUID(server["id"]),
            severity="warning",
            status="firing",
            starts_at=now,
            labels={},
            annotations={},
        )
        db.add(event)
        db.flush()
        db.add(IncidentAlert(incident_id=fallback.id, alert_event_id=event.id))
        db.commit()
        fallback_id = str(fallback.id)
    queued = api(f"/incidents/{fallback_id}/analyze", "POST", {}, role="operator", expected=202)
    result = wait(
        lambda: api("/analyses/" + queued["id"]), lambda r: r["status"] not in ("queued", "running")
    )
    assert result["status"] == "completed", result
    assert "source_unavailable_continue_runtime" in result["result"]["data_gaps"]
    saved = {
        "incident_id": incident["id"],
        "conversation_id": conversation["id"],
        "run_id": run["id"],
        "analysis_id": analysis["id"],
        "snapshot_id": second["id"],
        "source_context": analysis["source_context"],
        "file_evidence": evidence,
        "fallback_analysis_id": queued["id"],
        "report_projection_verified": True,
        "rbac_verified": True,
        "actual_docker_id": identity["Id"],
        "image_id": identity["Image"],
    }
    output.write_text(json.dumps(saved, ensure_ascii=False, indent=2), encoding="utf-8")
    print("Source diagnosis, adoption, privacy, fallback and report evidence verified")


if __name__ == "__main__":
    main()
