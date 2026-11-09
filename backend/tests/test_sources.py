import hashlib
import subprocess
import time
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from alembic import command
from source_git_server import GitServer
from sqlalchemy import inspect, select, text

import app.services.sources.store as source_store
import app.workers.sources as source_worker
from app.core.config import get_settings
from app.db.session import SessionLocal, engine
from app.models import Application, Container, Server, SourceAuditLog
from app.services.sources.store import SourceUnavailable

IMAGE = "sha256:" + "b" * 64


@pytest.fixture
def source_paths(tmp_path, monkeypatch):
    settings = get_settings()
    root = tmp_path / "input"
    root.mkdir()
    monkeypatch.setattr(settings, "source_input_dir", root)
    monkeypatch.setattr(settings, "source_store_dir", tmp_path / "store")
    return root


def create_app(client, headers, **values):
    response = client.post(
        "/api/v1/applications",
        headers=headers,
        json={
            "code": "inference-api",
            "name": "推理服务",
            "entrypoint": "app.main:app",
            "path_mappings": [{"runtime_prefix": "/app/app", "repository_prefix": "backend/app"}],
            **values,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def run_sync(client, headers, app, **values):
    response = client.post(
        f"/api/v1/applications/{app['id']}/source-syncs", headers=headers, json=values
    )
    assert response.status_code == 202, response.text
    assert response.json()["status"] == "queued"
    assert source_worker.sync_once()
    response = client.get(f"/api/v1/source-snapshots/{response.json()['id']}", headers=headers)
    assert response.status_code == 200
    return response.json()


def test_local_snapshot_immutable_redacted_and_mapped(client, admin_headers, source_paths):
    project = source_paths / "service"
    (project / "backend/app").mkdir(parents=True)
    original = 'timeout = 10\napi_key = "a-very-secret-value"\nprint(timeout)\n'
    (project / "backend/app/main.py").write_bytes(original.encode())
    (project / "backend/app/source_git_credentials.py").write_text(
        'token = os.getenv("TOKEN")\npassword = get_password()\nprint("password=" + token)\n',
        encoding="utf-8",
    )
    (project / ".env").write_text("PASSWORD=do-not-copy")
    (project / "private.key").write_text("do-not-copy")
    app = create_app(client, admin_headers, local_path="service", source_subdir="backend")
    snap = run_sync(client, admin_headers, app)
    assert snap["status"] == "ready" and snap["source"] == "local"
    assert snap["file_count"] == 2 and snap["git_commit"] is None
    assert "local_source_version_unverified" in snap["data_gaps"]
    path = f"/api/v1/source-snapshots/{snap['id']}"
    tree = client.get(path + "/tree", headers=admin_headers).json()
    assert [x["path"] for x in tree["files"]] == [
        "backend/app/main.py",
        "backend/app/source_git_credentials.py",
    ]
    module = client.get(
        path + "/file",
        params={"path": "backend/app/source_git_credentials.py"},
        headers=admin_headers,
    ).json()
    assert 'os.getenv("TOKEN")' in module["content"] and "get_password()" in module["content"]
    assert 'print("password=" + token)' in module["content"]
    assert tree["files"][0]["sha256"] == hashlib.sha256(original.encode()).hexdigest()
    (project / "backend/app/main.py").write_text("timeout = 90\n")
    result = client.get(
        path + "/file", params={"path": "backend/app/main.py"}, headers=admin_headers
    ).json()
    assert "timeout = 10" in result["content"] and "a-very-secret-value" not in result["content"]
    assert result["total_lines"] == 3
    search = client.get(path + "/search", params={"q": "PRINT"}, headers=admin_headers).json()
    assert search["matches"][0]["line"] == 3
    mapped = client.get(
        f"/api/v1/applications/{app['id']}/resolve-source-path",
        params={"runtime_path": "/app/app/main.py"},
        headers=admin_headers,
    ).json()
    assert mapped["path"] == "backend/app/main.py"
    mapped = client.get(
        f"/api/v1/applications/{app['id']}/resolve-source-path",
        params={"runtime_path": "/app/application/main.py"},
        headers=admin_headers,
    ).json()
    assert mapped["availability"] == "unavailable"
    second = run_sync(client, admin_headers, app)
    changes = client.get(
        path + "/diff", params={"other_id": second["id"]}, headers=admin_headers
    ).json()
    assert changes["changes"] == [{"path": "backend/app/main.py", "change": "modified"}]


def test_git_failure_falls_back_and_missing_sources_do_not_break_api(
    client,
    admin_headers,
    source_paths,
    monkeypatch,
):
    (source_paths / "main.py").write_text("answer = 42\n")

    def offline(*args, **kwargs):
        raise SourceUnavailable("git_fetch_or_object_unavailable")

    monkeypatch.setattr(source_store, "capture_git", offline)
    app = create_app(
        client, admin_headers, git_url="https://git.example/project.git", local_path="."
    )
    snap = run_sync(client, admin_headers, app, expected_commit="c" * 40)
    assert snap["source"] == "local" and snap["git_commit"] is None
    assert "git_fetch_or_object_unavailable" in snap["data_gaps"]
    assert snap["status"] == "partial"
    missing = create_app(client, admin_headers, code="no-source", local_path="missing")
    failed = run_sync(client, admin_headers, missing)
    assert failed["status"] == "failed" and failed["error_code"] == "local_directory_unavailable"
    assert client.get("/health/ready").status_code == 200
    no_source = create_app(client, admin_headers, code="third-party")
    failed = run_sync(client, admin_headers, no_source)
    assert failed["error_code"] == "no_source_available"
    response = client.post(
        f"/api/v1/applications/{no_source['id']}/releases",
        headers=admin_headers,
        json={"version": "1.0", "image_id": IMAGE},
    )
    assert response.status_code == 201  # Source is optional for release registration.


def test_credentials_package_code_retained_but_runtime_secrets_excluded(
    client,
    admin_headers,
    source_paths,
):
    package = source_paths / "credentials"
    package.mkdir()
    (package / "provider.py").write_text('token = os.getenv("TOKEN")\n', encoding="utf-8")
    (package / "runtime.json").write_text('{"token":"do-not-capture"}', encoding="utf-8")
    (package / "runtime-secret").write_text("do-not-capture", encoding="utf-8")
    app = create_app(client, admin_headers, local_path=".")
    snapshot = run_sync(client, admin_headers, app)
    tree = client.get(
        f"/api/v1/source-snapshots/{snapshot['id']}/tree", headers=admin_headers
    ).json()
    assert [f["path"] for f in tree["files"]] == ["credentials/provider.py"]


@pytest.mark.parametrize("path", ["../outside", "/etc", "C:/project", "a/../../b", "a\\b"])
def test_source_paths_reject_escape(client, admin_headers, path):
    response = client.post(
        "/api/v1/applications",
        headers=admin_headers,
        json={"code": "bad-path", "name": "bad", "local_path": path},
    )
    assert response.status_code == 422


def test_symlink_limits_and_integrity(client, admin_headers, source_paths, monkeypatch):
    settings = get_settings()
    (source_paths / "ok.py").write_text("value = 1\n")
    (source_paths / "big.py").write_text("x" * 2000)
    (source_paths / "binary.bin").write_bytes(b"\x00\x01")
    monkeypatch.setattr(settings, "source_max_file_bytes", 1024)
    app = create_app(client, admin_headers, local_path=".")
    snap = run_sync(client, admin_headers, app)
    assert snap["status"] == "partial"
    assert "file_size_limit" in snap["data_gaps"]
    assert "binary_or_non_utf8_files_excluded" in snap["data_gaps"]
    endpoint = f"/api/v1/source-snapshots/{snap['id']}/file"
    assert (
        client.get(endpoint, params={"path": "../outside"}, headers=admin_headers).status_code
        == 422
    )
    assert (
        client.get(
            endpoint, params={"path": "ok.py", "end_line": 201}, headers=admin_headers
        ).status_code
        == 422
    )
    stored = settings.source_store_dir / "snapshots" / snap["id"] / "ok.py"
    stored.write_text("corrupted")
    result = client.get(endpoint, params={"path": "ok.py"}, headers=admin_headers).json()
    assert result["reason"] == "snapshot_integrity_error"


@pytest.mark.parametrize("role", ["viewer", "operator", "admin"])
def test_source_permissions_and_encrypted_token(
    client,
    make_user,
    login,
    admin_headers,
    source_paths,
    role,
):
    token = "git-test-credential-not-for-output"
    app = create_app(
        client,
        admin_headers,
        git_url="https://git.example/private.git",
        git_username="reader",
        git_token=token,
    )
    assert app["has_git_token"] and token not in str(app)
    with SessionLocal() as db:
        row = db.get(Application, UUID(app["id"]))
        assert row.encrypted_git_token and token not in row.encrypted_git_token
    make_user("source-" + role, role)
    headers = {"Authorization": "Bearer " + login("source-" + role)["access_token"]}
    assert client.get("/api/v1/applications", headers=headers).status_code == 200
    updated = client.get(f"/api/v1/applications/{app['id']}", headers=headers)
    assert token not in updated.text and "encrypted_git_token" not in updated.text
    sync = client.post(f"/api/v1/applications/{app['id']}/source-syncs", json={}, headers=headers)
    assert sync.status_code == (403 if role == "viewer" else 202)
    changed = client.put(
        f"/api/v1/applications/{app['id']}",
        headers=headers,
        json={"code": "inference-api", "name": "changed"},
    )
    assert changed.status_code == (200 if role == "admin" else 403)
    with SessionLocal() as db:
        assert token not in str([r.detail for r in db.scalars(select(SourceAuditLog))])


def make_container(name="api", image_id=IMAGE):
    with SessionLocal() as db:
        server = db.scalar(select(Server))
        if server is None:
            server = Server(
                name="source-host",
                host="localhost",
                environment="test",
                node_exporter_target="localhost:9100",
                cadvisor_target="localhost:8081",
            )
            db.add(server)
            db.flush()
        container = Container(
            server_id=server.id,
            docker_id="/docker/" + uuid4().hex * 2,
            name=name,
            image="shared:local",
            status="running",
            labels={},
            image_id=image_id,
            identity={},
            identity_observed_at=datetime.now(UTC),
            last_seen_at=datetime.now(UTC),
        )
        db.add(container)
        db.commit()
        return str(container.id)


def test_shared_image_distinct_services_historical_deployment(client, admin_headers):
    api_app = create_app(client, admin_headers)
    worker = create_app(client, admin_headers, code="inference-worker", entrypoint="app.workers.ai")
    api_container, worker_container = make_container("api"), make_container("worker")
    now = datetime.now(UTC)
    deployments = []
    for app, target in ((api_app, api_container), (worker, worker_container)):
        release = client.post(
            f"/api/v1/applications/{app['id']}/releases",
            headers=admin_headers,
            json={"version": "v1", "image_id": IMAGE},
        ).json()
        response = client.post(
            f"/api/v1/applications/{app['id']}/deployments",
            headers=admin_headers,
            json={
                "release_id": release["id"],
                "container_id": target,
                "environment": "test",
                "started_at": now.isoformat(),
                "compose_project": "demo",
                "compose_service": app["code"],
            },
        )
        assert response.status_code == 201, response.text
        deployment = response.json()
        assert deployment["binding"]["status"] == "verified"
        assert deployment["binding"]["entrypoint"] == app["entrypoint"]
        deployments.append(deployment)
    assert deployments[0]["binding"]["service_code"] != deployments[1]["binding"]["service_code"]
    # An overlapping declaration for the same instance is rejected even across applications.
    response = client.post(
        f"/api/v1/applications/{worker['id']}/deployments",
        headers=admin_headers,
        json={
            "release_id": deployments[1]["release_id"],
            "container_id": api_container,
            "environment": "test",
            "started_at": now.isoformat(),
        },
    )
    assert response.status_code == 409
    # A rebuilt running image must not rewrite the saved deployment evidence.
    with SessionLocal() as db:
        row = db.get(Container, UUID(api_container))
        row.image_id = "sha256:" + "d" * 64
        db.commit()
    result = client.get(
        f"/api/v1/containers/{api_container}/deployments", headers=admin_headers
    ).json()[0]
    assert result["binding"]["status"] == "verified"
    assert result["binding"]["observed_image_id"] == IMAGE
    assert result["current_identity"]["status"] == "mismatch"
    assert (
        client.delete(f"/api/v1/applications/{api_app['id']}", headers=admin_headers).status_code
        == 409
    )
    result = client.post(
        f"/api/v1/application-deployments/{deployments[0]['id']}/end",
        headers=admin_headers,
        json={"ended_at": datetime.now(UTC).isoformat()},
    )
    assert result.status_code == 200
    assert (
        client.delete(f"/api/v1/applications/{api_app['id']}", headers=admin_headers).status_code
        == 204
    )


def test_release_source_freezes_and_cross_application_link_rejected(
    client,
    admin_headers,
    source_paths,
):
    (source_paths / "main.py").write_text("value = 1\n")
    app = create_app(client, admin_headers, local_path=".")
    snap = run_sync(client, admin_headers, app)
    other = create_app(client, admin_headers, code="other", local_path="different")
    response = client.post(
        f"/api/v1/applications/{other['id']}/releases",
        headers=admin_headers,
        json={"version": "v1", "snapshot_id": snap["id"]},
    )
    assert response.status_code == 422
    shared = create_app(client, admin_headers, code="shared-worker", local_path=".")
    shared_release = client.post(
        f"/api/v1/applications/{shared['id']}/releases",
        headers=admin_headers,
        json={"version": "v1", "snapshot_id": snap["id"]},
    )
    assert shared_release.status_code == 201
    shared_snapshots = client.get(
        f"/api/v1/applications/{shared['id']}/snapshots", headers=admin_headers
    ).json()
    assert shared_snapshots[0]["id"] == snap["id"]
    release = client.post(
        f"/api/v1/applications/{app['id']}/releases",
        headers=admin_headers,
        json={"version": "v1", "snapshot_id": snap["id"]},
    )
    assert release.status_code == 201
    duplicate = client.post(
        f"/api/v1/applications/{app['id']}/releases", headers=admin_headers, json={"version": "v1"}
    )
    assert duplicate.status_code == 409
    second = run_sync(client, admin_headers, app)
    replace = client.post(
        f"/api/v1/application-releases/{release.json()['id']}/attach-snapshot",
        headers=admin_headers,
        params={"snapshot_id": second["id"]},
    )
    assert replace.status_code == 409


def test_fixed_git_objects_ignore_dirty_worktree_and_branch_moves(tmp_path, monkeypatch):
    repository = tmp_path / "repo"
    repository.mkdir()

    def git(*args):
        return (
            subprocess.run(["git", "-C", str(repository), *args], check=True, capture_output=True)
            .stdout.decode()
            .strip()
        )

    git("init")
    (repository / "main.py").write_text("value = 1\n")
    git("add", "main.py")
    git("-c", "user.name=SourceTest", "-c", "user.email=test@example.invalid", "commit", "-m", "v1")
    first = git("rev-parse", "HEAD")
    (repository / "main.py").write_text("value = 2\n")
    git("add", "main.py")
    git("-c", "user.name=SourceTest", "-c", "user.email=test@example.invalid", "commit", "-m", "v2")
    (repository / "main.py").write_text("dirty = True\n")
    destination = tmp_path / "snapshot"
    destination.mkdir()
    manifest, gaps, _, commit = source_store.capture_git_tree(
        repository,
        first,
        destination,
        time.monotonic() + 30,
    )
    assert commit == first and not gaps
    assert (destination / "main.py").read_text() == "value = 1\n"
    assert manifest[0]["path"] == "main.py"


def test_sync_deduplicates_same_request_without_losing_other_commits(client, admin_headers):
    app = create_app(client, admin_headers, git_url="https://git.example/repo.git")
    url = f"/api/v1/applications/{app['id']}/source-syncs"
    first = client.post(url, headers=admin_headers, json={"expected_commit": "a" * 40})
    repeat = client.post(url, headers=admin_headers, json={"expected_commit": "a" * 40})
    second = client.post(url, headers=admin_headers, json={"expected_commit": "b" * 40})
    assert first.status_code == repeat.status_code == second.status_code == 202
    assert first.json()["id"] == repeat.json()["id"]
    assert first.json()["id"] != second.json()["id"]


def test_source_migration_metadata_and_timestamp_triggers(migrated_database):
    command.check(migrated_database)
    inspector = inspect(engine)
    for table in (
        "applications",
        "source_snapshots",
        "application_releases",
        "application_deployments",
        "source_audit_logs",
    ):
        columns = {c["name"]: c for c in inspector.get_columns(table)}
        assert not columns["created_at"]["nullable"] and not columns["updated_at"]["nullable"]
        assert columns["deleted_at"]["nullable"]
        assert columns["created_at"]["type"].timezone and columns["updated_at"]["type"].timezone
        with engine.connect() as connection:
            assert (
                connection.scalar(
                    text("SELECT count(*) FROM pg_trigger WHERE tgname=:name AND NOT tgisinternal"),
                    {"name": f"trg_{table}_updated_at"},
                )
                == 1
            )


@pytest.mark.parametrize("private", [False, True])
def test_real_https_git_sync_authentication_and_offline_cache(
    client,
    admin_headers,
    source_paths,
    tmp_path,
    monkeypatch,
    private,
):
    repository = tmp_path / "work"
    repository.mkdir()

    def git(*args):
        return (
            subprocess.run(["git", "-C", str(repository), *args], check=True, capture_output=True)
            .stdout.decode()
            .strip()
        )

    git("init")
    (repository / "main.py").write_bytes(b"answer = 42\n")
    git("add", "main.py")
    git(
        "-c",
        "user.name=SourceTest",
        "-c",
        "user.email=test@example.invalid",
        "commit",
        "-m",
        "source",
    )
    commit = git("rev-parse", "HEAD")
    remote = tmp_path / "remote"
    remote.mkdir()
    subprocess.run(
        ["git", "clone", "--bare", str(repository), str(remote / "repo.git")],
        capture_output=True,
        check=True,
    )
    credentials = ("reader", "fixture-https-git-credential") if private else None
    with GitServer(remote, credentials) as server:
        monkeypatch.setattr(get_settings(), "source_git_ca_file", server.ca_file)
        app = create_app(
            client,
            admin_headers,
            git_url=server.url,
            **({"git_username": credentials[0], "git_token": credentials[1]} if private else {}),
        )
        snapshot = run_sync(client, admin_headers, app)
        assert snapshot["status"] == "ready", snapshot["error_code"]
        assert snapshot["source"] == "git" and snapshot["git_commit"] == commit
        if private:
            assert True in server.attempts
            assert credentials[1] not in str(snapshot)
    cached = run_sync(client, admin_headers, app, expected_commit=commit)
    assert cached["status"] == "ready" and cached["git_commit"] == commit
    assert cached["content_hash"] == snapshot["content_hash"]
