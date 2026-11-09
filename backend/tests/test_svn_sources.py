import copy
import time
from datetime import UTC, datetime
from uuid import UUID

import pytest
from alembic import command
from pydantic import ValidationError
from source_svn_server import SvnServer
from sqlalchemy import select
from test_sources import IMAGE, create_app, make_container, run_sync
from test_sources import source_paths as source_paths

import app.integrations.svn as svn_integration
import app.services.sources.store as source_store
from app.core.config import get_settings
from app.core.secrets import cipher
from app.db.session import SessionLocal
from app.models import Application, SourceAuditLog, SourceSnapshot
from app.schemas.sources import ApplicationInput, ReleaseInput, SyncInput
from app.services.sources.diagnostics import (
    SourceFile,
    context_hash,
    dispatch_source,
    source_evidence_valid,
    source_identity,
)
from app.services.sources.store import SourceUnavailable


@pytest.mark.parametrize("private", [False, True])
def test_https_svn_fixed_revision_browsing_and_release(
    client,
    admin_headers,
    source_paths,
    tmp_path,
    monkeypatch,
    private,
):
    credentials = ("reader", "svn-fixture-password") if private else None
    with SvnServer(tmp_path, credentials) as server:
        work = server.work
        (work / "main.py").write_text('answer = 1\napi_key = "do-not-expose"\n')
        (work / "说明 @ v1.py").write_text("name = 'unicode path'\n")
        (work / ".env").write_text("PASSWORD=do-not-capture\n")
        (work / "node_modules").mkdir()
        (work / "node_modules/ignored.js").write_text("ignored\n")
        (work / "pointer").symlink_to("main.py")
        server.svn("add", "--force", ".")
        server.svn("propset", "svn:externals", "https://127.0.0.1:1/unused external", ".")
        server.commit("v1")
        (work / "main.py").write_text("answer = 2\n")
        server.commit("v2")
        app = create_app(
            client,
            admin_headers,
            svn_url=server.url,
            **({"svn_username": credentials[0], "svn_password": credentials[1]} if private else {}),
        )
        # An untrusted certificate fails; no automatic TLS bypass is allowed.
        untrusted = run_sync(client, admin_headers, app, source="svn")
        assert untrusted["status"] == "failed"
        monkeypatch.setattr(get_settings(), "source_svn_ca_file", server.ca_file)
        commands = []
        original = svn_integration.subprocess.Popen

        def record_command(command, *args, **kwargs):
            commands.append(command)
            return original(command, *args, **kwargs)

        monkeypatch.setattr(svn_integration.subprocess, "Popen", record_command)
        first = run_sync(client, admin_headers, app, expected_revision="1")
        assert first["source"] == "svn" and first["svn_revision"] == "1"
        assert first["git_commit"] is None and first["status"] == "partial"
        assert "svn_externals_not_downloaded" in first["data_gaps"]
        assert "symlinks_excluded" in first["data_gaps"]
        endpoint = f"/api/v1/source-snapshots/{first['id']}"
        tree = client.get(endpoint + "/tree", headers=admin_headers).json()
        assert {item["path"] for item in tree["files"]} == {"main.py", "说明 @ v1.py"}
        file = client.get(
            endpoint + "/file", params={"path": "main.py"}, headers=admin_headers
        ).json()
        assert file["svn_revision"] == "1" and "answer = 1" in file["content"]
        assert "do-not-expose" not in file["content"]
        unicode_file = client.get(
            endpoint + "/file",
            params={"path": "说明 @ v1.py"},
            headers=admin_headers,
        ).json()
        assert unicode_file["availability"] == "available"
        hits = client.get(
            endpoint + "/search", params={"q": "answer"}, headers=admin_headers
        ).json()
        assert hits["matches"][0]["line"] == 1
        latest = run_sync(client, admin_headers, app)
        assert latest["svn_revision"] == "2"
        diff = client.get(
            endpoint + "/diff",
            params={"other_id": latest["id"], "path": "main.py"},
            headers=admin_headers,
        ).json()
        assert "+answer = 2" in diff["diff"]
        release = client.post(
            f"/api/v1/applications/{app['id']}/releases",
            headers=admin_headers,
            json={"version": "v1", "snapshot_id": first["id"], "image_id": IMAGE},
        )
        assert release.status_code == 201, release.text
        assert release.json()["expected_revision"] == "1"
        deployment = client.post(
            f"/api/v1/applications/{app['id']}/deployments",
            headers=admin_headers,
            json={
                "release_id": release.json()["id"],
                "container_id": make_container(),
                "environment": "test",
                "started_at": datetime.now(UTC).isoformat(),
            },
        )
        assert deployment.status_code == 201, deployment.text
        binding = deployment.json()["binding"]
        assert binding["svn_revision"] == binding["expected_revision"] == "1"
        assert binding["source_status"] == "fixed_svn" and binding["status"] == "verified"
        mismatch = client.post(
            f"/api/v1/applications/{app['id']}/releases",
            headers=admin_headers,
            json={"version": "bad", "snapshot_id": first["id"], "expected_revision": "2"},
        )
        assert mismatch.status_code == 422
        shared = create_app(client, admin_headers, code="shared", svn_url=server.url)
        shared_snapshots = client.get(
            f"/api/v1/applications/{shared['id']}/snapshots",
            headers=admin_headers,
        ).json()
        assert first["id"] in {item["id"] for item in shared_snapshots}
        shared_release = client.post(
            f"/api/v1/applications/{shared['id']}/releases",
            headers=admin_headers,
            json={"version": "shared", "snapshot_id": first["id"], "expected_revision": "1"},
        )
        assert shared_release.status_code == 201, shared_release.text
        with SessionLocal() as db:
            snapshot = db.get(SourceSnapshot, UUID(first["id"]))
            identity = source_identity(snapshot)
            context = {
                "snapshots": [{**identity, "application_id": app["id"], "path_mappings": []}]
            }
            evidence = dispatch_source(
                db,
                context,
                "read_source_file",
                SourceFile(snapshot_id=first["id"], path="main.py"),
            )
            assert evidence["source_refs"][0]["svn_revision"] == "1"
            assert source_evidence_valid(
                db, context, {"source": "read_source_file", "data": evidence}
            )
            altered = copy.deepcopy(evidence)
            altered["source_refs"][0]["svn_revision"] = "2"
            assert not source_evidence_valid(
                db, context, {"source": "read_source_file", "data": altered}
            )
            altered_context = copy.deepcopy(context)
            altered_context["snapshots"][0]["svn_revision"] = "2"
            assert context_hash(altered_context) != context_hash(context)
            assert (
                dispatch_source(
                    db,
                    altered_context,
                    "read_source_file",
                    SourceFile(snapshot_id=first["id"], path="main.py"),
                )["reason"]
                == "snapshot_identity_changed"
            )
        if private:
            assert all(credentials[1] not in repr(command) for command in commands)
            assert app["has_svn_password"] and credentials[1] not in str(app)
            with SessionLocal() as db:
                saved = db.get(Application, UUID(app["id"]))
                assert credentials[1] not in saved.encrypted_svn_password
                assert (
                    cipher().decrypt(saved.encrypted_svn_password.encode()).decode()
                    == credentials[1]
                )
                assert credentials[1] not in str(snapshot.request)
                assert credentials[1] not in str(
                    [r.detail for r in db.scalars(select(SourceAuditLog))]
                )
    # Reading stored SVN source does not depend on the remote remaining online.
    assert (
        client.get(endpoint + "/file", params={"path": "main.py"}, headers=admin_headers).json()[
            "availability"
        ]
        == "available"
    )


def test_svn_failure_fallback_and_queue_identity(client, admin_headers, source_paths, monkeypatch):
    (source_paths / "main.py").write_text("answer = 42\n")

    def unavailable(*_args, **_kwargs):
        raise SourceUnavailable("svn_remote_or_revision_unavailable")

    monkeypatch.setattr(source_store, "capture_svn", unavailable)
    app = create_app(client, admin_headers, svn_url="https://svn.example/trunk", local_path=".")
    fallback = run_sync(client, admin_headers, app, expected_revision="12")
    assert fallback["source"] == "local" and fallback["svn_revision"] is None
    assert "svn_remote_or_revision_unavailable" in fallback["data_gaps"]
    strict = run_sync(client, admin_headers, app, source="svn")
    assert strict["status"] == "failed"
    url = f"/api/v1/applications/{app['id']}/source-syncs"
    first = client.post(url, headers=admin_headers, json={"expected_revision": "12"})
    repeat = client.post(url, headers=admin_headers, json={"expected_revision": "12"})
    second = client.post(url, headers=admin_headers, json={"expected_revision": "13"})
    assert first.json()["id"] == repeat.json()["id"] != second.json()["id"]
    assert (
        client.post(url, headers=admin_headers, json={"expected_commit": "a" * 40}).status_code
        == 422
    )
    response = client.put(
        f"/api/v1/applications/{app['id']}",
        headers=admin_headers,
        json={"code": app["code"], "name": app["name"], "svn_url": "https://svn.example/other"},
    )
    assert response.status_code == 200
    import app.workers.sources as worker

    assert worker.sync_once()
    result = client.get(
        f"/api/v1/source-snapshots/{first.json()['id']}", headers=admin_headers
    ).json()
    assert result["error_code"] == "source_configuration_changed"


@pytest.mark.parametrize("role", ["viewer", "operator", "admin"])
def test_svn_credentials_permissions_and_rotation(client, admin_headers, make_user, login, role):
    secret = "svn-test-secret"
    values = {"svn_url": "https://svn.example/trunk", "svn_username": "reader"}
    app = create_app(client, admin_headers, **values, svn_password=secret)
    body = {"code": app["code"], "name": app["name"], **values}
    endpoint = f"/api/v1/applications/{app['id']}"
    preserved = client.put(endpoint, headers=admin_headers, json=body)
    assert preserved.json()["has_svn_password"]
    make_user("svn-" + role, role)
    headers = {"Authorization": "Bearer " + login("svn-" + role)["access_token"]}
    assert secret not in client.get(endpoint, headers=headers).text
    sync = client.post(endpoint + "/source-syncs", headers=headers, json={})
    assert sync.status_code == (403 if role == "viewer" else 202)
    rotated = client.put(endpoint, headers=headers, json={**body, "svn_username": "other"})
    assert rotated.status_code == (200 if role == "admin" else 403)
    if role == "admin":
        assert not rotated.json()["has_svn_password"]
    replaced = client.put(
        endpoint, headers=admin_headers, json={**body, "svn_password": "replacement"}
    )
    assert replaced.json()["has_svn_password"]
    removed = client.put(endpoint, headers=admin_headers, json={**body, "clear_svn_password": True})
    assert not removed.json()["has_svn_password"]


@pytest.mark.parametrize(
    "values",
    [
        {"svn_url": "svn://example/repo"},
        {"svn_url": "http://example/repo"},
        {"svn_url": "https://reader:secret@example/repo"},
        {"svn_url": "https://example/repo?key=secret"},
        {"svn_url": "https://example/repo", "git_url": "https://example/repo.git"},
        {"svn_revision": "-r1"},
        {"svn_revision": "1:2"},
        {"svn_revision": "01"},
        {"svn_password": "line\nbreak"},
        {"svn_username": "line\nbreak"},
    ],
)
def test_svn_connection_validation(values):
    with pytest.raises(ValidationError):
        ApplicationInput(code="svn", name="SVN", **values)


def test_svn_version_validation_and_timeout():
    with pytest.raises(ValidationError):
        SyncInput(expected_revision="1", expected_commit="a" * 40)
    with pytest.raises(ValidationError):
        SyncInput(source="git", expected_revision="1")
    with pytest.raises(ValidationError):
        ReleaseInput(version="bad", expected_revision="1", build_source_status="clean_git")
    with pytest.raises(SourceUnavailable, match="sync_timeout"):
        svn_integration.svn_command(["help"], time.monotonic() - 1)


def test_svn_command_output_limit():
    import shutil

    if not shutil.which("svn"):
        pytest.skip("requires SVN CLI")
    with pytest.raises(SourceUnavailable, match="svn_output_limit"):
        svn_integration.svn_command(["help"], time.monotonic() + 10, max_output=10)


def test_svn_migration_preserves_existing_source_records(client, admin_headers, migrated_database):
    app = create_app(client, admin_headers, git_url="https://git.example/project.git")
    with SessionLocal() as db:
        saved = db.get(Application, UUID(app["id"]))
        timestamps = saved.created_at, saved.updated_at, saved.deleted_at
    try:
        command.downgrade(migrated_database, "0022_service_observability")
        assert client.get("/health/ready").status_code == 503
        command.upgrade(migrated_database, "head")
        command.check(migrated_database)
        assert client.get("/health/ready").status_code == 200
        with SessionLocal() as db:
            saved = db.get(Application, UUID(app["id"]))
            assert saved.git_url == app["git_url"]
            assert (
                saved.svn_url is None and saved.svn_revision == "HEAD" and saved.svn_username == ""
            )
            assert (saved.created_at, saved.updated_at, saved.deleted_at) == timestamps
    finally:
        command.upgrade(migrated_database, "head")
