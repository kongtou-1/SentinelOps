"""Isolated real HTTP fault -> Harness -> handoff -> rebuilt image -> human verification."""

import argparse
import json
import subprocess
import sys
from datetime import UTC, datetime
from http.client import RemoteDisconnected
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import UUID, uuid4

from accept_source_diagnosis import wait
from accept_sources import request
from sqlalchemy import select

ROOT = Path(__file__).resolve().parents[2]
PASSWORD = "Repair-acceptance-2026"
FIXTURE = ROOT / ".local/repair-fixture"
OUTPUT = ROOT / ".local/repair-handoff-acceptance.json"
BASE = "http://127.0.0.1:18015"


def docker(*args):
    result = subprocess.run(["docker", *args], capture_output=True, text=True, check=True)
    return result.stdout if args[0] == "inspect" else result.stdout + result.stderr


def fixture_source(status):
    return f"""from http.server import BaseHTTPRequestHandler, HTTPServer

timeout = 20


def status_code():
    return {status}


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(status_code())
        self.end_headers()
        self.wfile.write(b"repair-fixture")

    def log_message(self, *_):
        pass


if __name__ == "__main__":
    HTTPServer(("0.0.0.0", 9000), Handler).serve_forever()
"""


def prepare_fixture():
    (FIXTURE / "backend/app").mkdir(parents=True, exist_ok=True)
    (FIXTURE / "tests").mkdir(exist_ok=True)
    (FIXTURE / "backend/app/main.py").write_text(fixture_source(503), encoding="utf-8")
    (FIXTURE / "tests/test_health.py").write_text(
        "import sys, unittest\nsys.path.insert(0, '/fixture')\nimport main\n"
        "class HealthTest(unittest.TestCase):\n"
        "    def test_status(self): self.assertEqual(main.status_code(), 200)\n",
        encoding="utf-8",
    )
    (FIXTURE / "Dockerfile").write_text(
        "FROM sentinelops-backend:local\nCOPY backend/app/main.py /fixture/main.py\n"
        "COPY tests /fixture/tests\nWORKDIR /fixture\n"
        'CMD ["/usr/local/bin/python", "main.py"]\n'
        'HEALTHCHECK --interval=1s --timeout=2s --retries=1 CMD ["/usr/local/bin/python", "-c", '
        "\"import urllib.request; urllib.request.urlopen('http://127.0.0.1:9000')\"]\n",
        encoding="utf-8",
    )


def http_observation():
    try:
        with urlopen("http://127.0.0.1:18035", timeout=3) as response:
            return {"status": response.status, "observed_at": datetime.now(UTC).isoformat()}
    except HTTPError as exc:
        return {"status": exc.code, "observed_at": datetime.now(UTC).isoformat()}
    except (URLError, RemoteDisconnected, TimeoutError, ConnectionResetError):
        return {"status": 0, "observed_at": datetime.now(UTC).isoformat()}


def main():
    sys.path.insert(0, str(ROOT / "backend"))
    from app.core.config import get_settings
    from app.core.secrets import file_secret
    from app.core.security import password_hasher
    from app.db.session import SessionLocal
    from app.models import Container, Role, User

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepare", action="store_true")
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    if get_settings().postgres_db != "ops_platform_repairs_acceptance_test":
        raise SystemExit("Only the dedicated repair acceptance database is allowed")
    if args.prepare:
        prepare_fixture()
        print("Isolated repair fixture prepared")
        return
    with SessionLocal() as db:
        for role in ("admin", "operator", "viewer"):
            name = "repair-" + role
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
    for role in ("admin", "operator", "viewer"):
        code, data = request(
            BASE,
            "/api/v1/auth/login",
            method="POST",
            body={"username": "repair-" + role, "password": PASSWORD},
        )
        assert code == 200
        tokens[role] = data["access_token"]

    def api(path, method="GET", body=None, role="admin", expected=200):
        code, data = request(BASE, "/api/v1" + path, tokens[role], method, body)
        assert code == expected, (path, code, data)
        return data

    if args.verify:
        saved = json.loads(OUTPUT.read_text(encoding="utf-8"))
        row = api("/repair-handoffs/" + saved["handoff"]["id"], role="viewer")
        assert row == saved["handoff"]
        report = api("/reports/" + saved["report_id"], role="viewer")
        assert report["snapshot_hash"] == saved["report_snapshot_hash"]
        queued = api(
            f"/incidents/{saved['incident_id']}/analyze",
            "POST",
            {"force": True},
            role="operator",
            expected=202,
        )
        result = wait(
            lambda: api("/analyses/" + queued["id"]),
            lambda r: r["status"] not in ("queued", "running"),
        )
        assert result["status"] == "completed", result
        assert result["source_context"] == saved["source_context"]
        assert any(e["source"] == "read_source_file" for e in result["evidence"])
        saved["restart_and_stopped_source_worker_verified"] = True
        OUTPUT.write_text(json.dumps(saved, ensure_ascii=False, indent=2), encoding="utf-8")
        print("Handoff, repair links, report snapshot and frozen source diagnosis survived restart")
        return

    request(
        "http://127.0.0.1:18034", "/scenario", method="POST", body={"scenario": "source_diagnosis"}
    )
    app = api(
        "/applications",
        "POST",
        {
            "code": "repair-fixture",
            "name": "修复交接验收服务",
            "local_path": ".",
            "entrypoint": "main.py",
            "path_mappings": [{"runtime_prefix": "/fixture", "repository_prefix": "backend/app"}],
        },
        expected=201,
    )
    server = api(
        "/servers",
        "POST",
        {
            "name": "repair-local-docker",
            "host": "localhost",
            "node_exporter_target": "node:9100",
            "cadvisor_target": "cadvisor:8080",
        },
        expected=201,
    )

    def sync():
        queued = api(
            f"/applications/{app['id']}/source-syncs", "POST", {}, role="operator", expected=202
        )
        result = wait(
            lambda: api("/source-snapshots/" + queued["id"]),
            lambda s: s["status"] not in ("queued", "running"),
        )
        assert result["status"] in ("ready", "partial"), result
        return result

    def register_container(name):
        identity = json.loads(docker("inspect", name))[0]
        with SessionLocal() as db:
            row = Container(
                server_id=UUID(server["id"]),
                docker_id="/docker/" + identity["Id"],
                name=name,
                image=identity["Config"]["Image"],
                status="running",
                labels={},
                last_seen_at=datetime.now(UTC),
                image_id=identity["Image"],
                identity_observed_at=datetime.now(UTC),
                identity={"compose_project": "sentinelops-repairs", "compose_service": "fixture"},
            )
            db.add(row)
            db.commit()
            return str(row.id), identity

    docker("build", "-t", "sentinelops-repair-fixture:v1", str(FIXTURE))
    docker(
        "run",
        "-d",
        "--name",
        "sentinelops-repairs-target-v1",
        "-p",
        "127.0.0.1:18035:9000",
        "sentinelops-repair-fixture:v1",
    )
    fault = wait(http_observation, lambda v: v["status"] == 503)
    first = sync()
    container1, identity1 = register_container("sentinelops-repairs-target-v1")
    release1 = api(
        f"/applications/{app['id']}/releases",
        "POST",
        {
            "version": "broken-v1",
            "snapshot_id": first["id"],
            "image_id": identity1["Image"],
            "change_summary": "隔离 fixture 返回 HTTP 503",
            "build_source_status": "local_snapshot",
        },
        expected=201,
    )
    deployment1 = api(
        f"/applications/{app['id']}/deployments",
        "POST",
        {
            "release_id": release1["id"],
            "container_id": container1,
            "environment": "test",
            "compose_project": "sentinelops-repairs",
            "compose_service": "fixture",
            "started_at": datetime.now(UTC).isoformat(),
        },
        expected=201,
    )
    alert = {
        "status": "firing",
        "fingerprint": uuid4().hex,
        "startsAt": datetime.now(UTC).isoformat(),
        "endsAt": "0001-01-01T00:00:00Z",
        "labels": {
            "server_id": server["id"],
            "id": "/docker/" + identity1["Id"],
            "severity": "warning",
            "alertname": "RepairFixtureHTTP503",
        },
        "annotations": {"summary": "隔离 HTTP 服务实际返回 503", "observation": json.dumps(fault)},
    }
    code, _ = request(
        BASE,
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
            "content": "请核对对应源码和故障证据；PRIVATE-REPAIR-CHAT",
        },
        role="operator",
        expected=202,
    )
    run = wait(
        lambda: api("/analysis-runs/" + message["run_id"], role="operator"),
        lambda r: r["status"] not in ("queued", "running", "cancelling"),
    )
    assert run["status"] == "completed", run
    # A private candidate cannot be exported by pretending its run ID is a formal analysis.
    api(
        f"/incidents/{incident['id']}/repair-handoffs",
        "POST",
        {"analysis_id": run["id"], "acceptance_criteria": ["HTTP 200"]},
        role="operator",
        expected=404,
    )
    analysis = api(f"/analysis-runs/{run['id']}/adopt", "POST", role="operator")
    handoff = api(
        f"/incidents/{incident['id']}/repair-handoffs",
        "POST",
        {
            "analysis_id": analysis["id"],
            "reproduction": "GET 本机隔离服务，观测 HTTP 503",
            "acceptance_criteria": ["单元测试状态码为 200", "新容器连续五次 HTTP 200"],
        },
        role="operator",
        expected=201,
    )
    assert "PRIVATE-REPAIR-CHAT" not in json.dumps(handoff)
    api("/repair-handoffs/" + handoff["id"], role="viewer")
    api(
        f"/repair-handoffs/{handoff['id']}/results",
        "POST",
        {"change_summary": "denied", "test_result": "denied"},
        role="viewer",
        expected=403,
    )
    (FIXTURE / "backend/app/main.py").write_text(fixture_source(200), encoding="utf-8")
    second = sync()
    docker("build", "-t", "sentinelops-repair-fixture:v2", str(FIXTURE))
    tests = docker(
        "run",
        "--rm",
        "--entrypoint",
        "/usr/local/bin/python",
        "sentinelops-repair-fixture:v2",
        "-m",
        "unittest",
        "discover",
        "-s",
        "tests",
        "-v",
    )
    docker("stop", "sentinelops-repairs-target-v1")
    api(
        f"/application-deployments/{deployment1['id']}/end",
        "POST",
        {"ended_at": datetime.now(UTC).isoformat()},
    )
    docker(
        "run",
        "-d",
        "--name",
        "sentinelops-repairs-target-v2",
        "-p",
        "127.0.0.1:18035:9000",
        "sentinelops-repair-fixture:v2",
    )
    wait(http_observation, lambda v: v["status"] == 200)
    container2, identity2 = register_container("sentinelops-repairs-target-v2")
    assert identity1["Id"] != identity2["Id"] and identity1["Image"] != identity2["Image"]
    release2 = api(
        f"/applications/{app['id']}/releases",
        "POST",
        {
            "version": "fixed-v2",
            "snapshot_id": second["id"],
            "image_id": identity2["Image"],
            "change_summary": "修复 fixture 状态码 503 -> 200",
            "test_result": "unittest: 1 passed",
            "build_source_status": "local_snapshot",
        },
        expected=201,
    )
    deployment2 = api(
        f"/applications/{app['id']}/deployments",
        "POST",
        {
            "release_id": release2["id"],
            "container_id": container2,
            "environment": "test",
            "compose_project": "sentinelops-repairs",
            "compose_service": "fixture",
            "started_at": datetime.now(UTC).isoformat(),
        },
        expected=201,
    )
    assert deployment2["binding"]["status"] == "verified"
    handoff = api(
        f"/repair-handoffs/{handoff['id']}/results",
        "POST",
        {
            "change_summary": "独立开发 fixture 将返回码改为 200；重建镜像并替换测试容器",
            "test_result": "docker run v2 python -m unittest discover -s tests -v: 1 passed",
            "release_id": release2["id"],
            "deployment_id": deployment2["id"],
        },
        role="operator",
        expected=201,
    )
    observations = [http_observation() for _ in range(5)]
    assert all(v["status"] == 200 for v in observations)
    handoff = api(
        f"/repair-handoffs/{handoff['id']}/verifications",
        "POST",
        {
            "result_id": handoff["results"][0]["id"],
            "outcome": "fix_validated",
            "observed_at": observations[-1]["observed_at"],
            "evidence": json.dumps(observations),
            "acceptance_results": "单元测试 1 passed；五次实际 HTTP GET 全部 200",
        },
        role="operator",
        expected=201,
    )
    assert handoff["packet"]["source_context"] == analysis["source_context"]
    assert "return 503" in next(
        e["data"]["content"]
        for e in handoff["packet"]["evidence"]
        if e["source"] == "read_source_file"
    )
    json_export = api(f"/repair-handoffs/{handoff['id']}/export?format=json", role="viewer")
    assert json_export["packet_hash"] == handoff["packet_hash"]

    # Keep the actual incident open: manual repair verification must not promote alert recovery.
    assert api("/incidents/" + incident["id"])["status"] == "open"
    from app.models import Incident, PostmortemReport
    from app.services.reports.logic import snapshot
    from app.utils.canonical_json import digest

    with SessionLocal() as db:
        captured = snapshot(db, db.get(Incident, UUID(incident["id"])))
        projected = next(r for r in captured["records"] if r["kind"] == "repair_handoff")
        assert projected["data"]["packet"] == handoff["packet"]
        # Persist test evidence for restart QA; this is not an approved report.
        report = PostmortemReport(
            incident_id=UUID(incident["id"]),
            requested_by=db.scalar(select(User.id).where(User.username == "repair-admin")),
            status="failed",
            error_code="acceptance_evidence_fixture",
            snapshot=captured,
            snapshot_hash=digest(captured),
            config={},
        )
        db.add(report)
        db.commit()
        report_id, report_hash = str(report.id), report.snapshot_hash
    saved = {
        "incident_id": incident["id"],
        "analysis_id": analysis["id"],
        "conversation_id": conversation["id"],
        "source_context": analysis["source_context"],
        "handoff": handoff,
        "fault": fault,
        "recovery": observations,
        "old_container": identity1["Id"],
        "new_container": identity2["Id"],
        "old_image": identity1["Image"],
        "new_image": identity2["Image"],
        "report_id": report_id,
        "report_snapshot_hash": report_hash,
        "report_evidence_projection_verified": True,
        "unit_test": tests,
        "rbac_and_private_boundary_verified": True,
    }
    OUTPUT.write_text(json.dumps(saved, ensure_ascii=False, indent=2), encoding="utf-8")
    # Save reviewable Markdown without embedding authorization in a URL.
    with urlopen(
        Request(
            BASE + f"/api/v1/repair-handoffs/{handoff['id']}/export?format=markdown",
            headers={"Authorization": "Bearer " + tokens["viewer"]},
        )
    ) as response:
        (ROOT / ".local/repair-handoff-example.md").write_bytes(response.read())
    print("Real HTTP 503 -> adopted source diagnosis -> handoff -> new image -> HTTP 200 verified")


if __name__ == "__main__":
    main()
