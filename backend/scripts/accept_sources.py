"""Local Docker acceptance against a dedicated *_sources_acceptance_test database."""

import argparse
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from uuid import uuid4

from sqlalchemy import select

PASSWORD = "Source-acceptance-2026"


def request(base, path, token=None, method="GET", body=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    data = json.dumps(body).encode() if body is not None else None
    try:
        with urlopen(
            Request(base + path, data=data, headers=headers, method=method), timeout=15
        ) as r:
            raw = r.read()
            return r.status, json.loads(raw) if raw else None
    except HTTPError as exc:
        return exc.code, json.loads(exc.read())


def main():
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from app.core.config import get_settings
    from app.core.security import password_hasher
    from app.db.session import SessionLocal
    from app.models import Role, User

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:18013")
    parser.add_argument("--output", type=Path, default=Path("../.local/sources-acceptance.json"))
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    if not get_settings().postgres_db.endswith("_sources_acceptance_test"):
        raise SystemExit("This acceptance only operates on *_sources_acceptance_test")
    with SessionLocal() as db:
        for role in ("admin", "operator", "viewer"):
            username = "source-" + role
            if not db.scalar(select(User.id).where(User.username == username)):
                record = db.scalar(select(Role).where(Role.code == role))
                db.add(
                    User(
                        username=username,
                        password_hash=password_hasher.hash(PASSWORD),
                        role_id=record.id,
                        is_active=True,
                    )
                )
        db.commit()
    tokens = {}
    for role in ("admin", "operator", "viewer"):
        code, data = request(
            args.url,
            "/api/v1/auth/login",
            method="POST",
            body={"username": "source-" + role, "password": PASSWORD},
        )
        assert code == 200, ("login", role, code)
        tokens[role] = data["access_token"]

    def api(path, method="GET", body=None, role="admin", expected=200):
        code, data = request(args.url, "/api/v1" + path, tokens[role], method, body)
        assert code == expected, (path, code, data)
        return data

    if args.verify:
        saved = json.loads(args.output.read_text())
        snapshot = api("/source-snapshots/" + saved["snapshot_id"])
        assert snapshot["content_hash"] == saved["content_hash"]
        content = api(f"/source-snapshots/{saved['snapshot_id']}/file?path=backend/app/main.py")
        assert content["availability"] == "available" and "source_router" in content["content"]
        for app_id in saved["application_ids"]:
            assert api(f"/applications/{app_id}/deployments")
        assert request(args.url, "/health/ready")[0] == 200
        print("Source snapshot and deployment history survived API/source-worker restart")
        return

    servers = api("/servers?limit=100")
    server = next((s for s in servers if s["name"] == "sources-phase1-local"), None)
    if server is None:
        server = api(
            "/servers",
            "POST",
            {
                "name": "sources-phase1-local",
                "host": "localhost",
                "environment": "test",
                "node_exporter_target": "node-exporter:9100",
                "cadvisor_target": "cadvisor:8080",
                "docker_state_target": "docker-state:9400",
            },
            expected=201,
        )
    deadline = time.monotonic() + 120
    containers = []
    while time.monotonic() < deadline:
        code, data = request(
            args.url, f"/api/v1/servers/{server['id']}/sync-containers", tokens["operator"], "POST"
        )
        if code == 200:
            containers = data
            selected = [
                c
                for c in containers
                if c.get("identity", {}).get("compose_service") in ("backend", "source-worker")
            ]
            if len(selected) == 2 and all(c["image_id"] for c in selected):
                break
        time.sleep(2)
    else:
        raise AssertionError("Fresh Docker identities were not discovered through Prometheus")

    apps = []
    for code, name, entry in (
        ("sources-phase1-api", "源码一期 API", "app.main:app"),
        ("sources-phase1-worker", "源码一期 worker", "app.workers.sources"),
    ):
        existing = api("/applications?q=" + code)
        app = next((a for a in existing if a["code"] == code), None)
        if app is None:
            app = api(
                "/applications",
                "POST",
                {
                    "code": code,
                    "name": name,
                    "entrypoint": entry,
                    "local_path": ".",
                    "source_subdir": "backend/app",
                    "path_mappings": [
                        {"runtime_prefix": "/app/app", "repository_prefix": "backend/app"}
                    ],
                },
                expected=201,
            )
        apps.append(app)
    job = api(
        f"/applications/{apps[0]['id']}/source-syncs",
        "POST",
        {"source": "local"},
        role="operator",
        expected=202,
    )
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        snapshot = api("/source-snapshots/" + job["id"])
        if snapshot["status"] in ("ready", "partial", "failed", "interrupted"):
            break
        time.sleep(1)
    assert snapshot["status"] in ("ready", "partial"), snapshot
    assert snapshot["source"] == "local" and snapshot["git_commit"] is None
    reader = api(f"/source-snapshots/{snapshot['id']}/file?path=backend/app/main.py", role="viewer")
    assert reader["availability"] == "available" and "source_router" in reader["content"]
    shared = api(f"/applications/{apps[1]['id']}/snapshots", role="viewer")
    assert any(s["id"] == snapshot["id"] for s in shared)
    version = "accept-" + uuid4().hex[:8]
    bindings = []
    for app, service in zip(apps, ("backend", "source-worker"), strict=True):
        target = next(
            c for c in containers if c.get("identity", {}).get("compose_service") == service
        )
        for prior in api(f"/applications/{app['id']}/deployments"):
            if prior["container_id"] == target["id"] and not prior["ended_at"]:
                api(
                    f"/application-deployments/{prior['id']}/end",
                    "POST",
                    {"ended_at": datetime.now(UTC).isoformat()},
                )
        release = api(
            f"/applications/{app['id']}/releases",
            "POST",
            {
                "version": version,
                "snapshot_id": snapshot["id"],
                "image_ref": target["image"],
                "image_id": target["image_id"],
                "change_summary": "本地源码一期验收；源码版本未核验，仅镜像身份核对",
                "test_result": "隔离验收",
            },
            expected=201,
        )
        deployment = api(
            f"/applications/{app['id']}/deployments",
            "POST",
            {
                "release_id": release["id"],
                "container_id": target["id"],
                "environment": "test",
                "started_at": datetime.now(UTC).isoformat(),
            },
            expected=201,
        )
        assert deployment["binding"]["status"] == "verified", deployment
        assert deployment["binding"]["source_status"] == "local_reference"
        assert deployment["compose_service"] == service
        bindings.append(deployment)
    assert bindings[0]["binding"]["entrypoint"] != bindings[1]["binding"]["entrypoint"]
    assert (
        bindings[0]["binding"]["observed_image_id"] == bindings[1]["binding"]["observed_image_id"]
    )
    api(f"/applications/{apps[0]['id']}/source-syncs", "POST", {}, role="viewer", expected=403)
    result = {
        "snapshot_id": snapshot["id"],
        "content_hash": snapshot["content_hash"],
        "application_ids": [a["id"] for a in apps],
        "deployment_ids": [b["id"] for b in bindings],
        "shared_image_id": bindings[0]["binding"]["observed_image_id"],
        "source_status": "local_reference",
        "identity_status": "verified",
        "role_checks": ["viewer_read", "viewer_sync_denied", "operator_sync", "admin_bind"],
    }
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        "Docker identity → distinct services → shared source snapshot → release/deployment passed"
    )


if __name__ == "__main__":
    main()
