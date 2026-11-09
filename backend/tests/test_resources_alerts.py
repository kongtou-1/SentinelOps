import io
import socket
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier, Event, Thread
from uuid import uuid4

import paramiko
import pytest
import yaml
from alembic import command
from cryptography.fernet import Fernet
from fastapi import HTTPException
from sqlalchemy import select

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.models import AlertEvent, Container, SSHCredential

TOKEN = "isolated-service-token-for-testing-2026"
SERVICE_HEADERS = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture(autouse=True)
def isolated_secrets(tmp_path, monkeypatch):
    settings = get_settings()
    for field, contents in (
        ("credential_key_file", Fernet.generate_key().decode()),
        ("monitoring_token_file", TOKEN),
        ("alertmanager_token_file", TOKEN),
    ):
        path = tmp_path / field
        path.write_text(contents, encoding="utf-8")
        monkeypatch.setattr(settings, field, path)
    monkeypatch.setattr(settings, "monitoring_rules_dir", tmp_path / "rules")


@pytest.fixture
def server_body():
    return {
        "name": "test-server",
        "host": "127.0.0.1",
        "node_exporter_target": "node-exporter:9100",
        "cadvisor_target": "cadvisor:8080",
        "health_url": "http://test-app:8001",
    }


@pytest.fixture
def server_id(client, admin_headers, server_body):
    response = client.post("/api/v1/servers", headers=admin_headers, json=server_body)
    assert response.status_code == 201, response.text
    return response.json()["id"]


def webhook_body(status="firing", start=None):
    start = start or datetime.now(UTC) - timedelta(minutes=1)
    return {
        "version": "4",
        "alerts": [
            {
                "fingerprint": "abc123",
                "status": status,
                "startsAt": start.isoformat(),
                "endsAt": datetime.now(UTC).isoformat()
                if status == "resolved"
                else "0001-01-01T00:00:00Z",
                "labels": {"alertname": "TestDown", "severity": "critical"},
                "annotations": {"summary": "Test down"},
            }
        ],
    }


@pytest.mark.parametrize("role", ["viewer", "operator", "admin"])
def test_resource_alert_permissions(client, make_user, login, role, server_body):
    make_user(role, role)
    auth = {"Authorization": f"Bearer {login(role)['access_token']}"}
    for path in ("/servers", "/containers", "/alert-rules", "/alerts"):
        assert client.get("/api/v1" + path, headers=auth).status_code == 200
    for path, body in (
        ("/servers", server_body),
        ("/ssh-credentials", {"name": "secret", "auth_type": "password", "secret": "password"}),
        ("/alert-rules", {"code": "TargetDown", "metric": "target_up", "threshold": 1}),
    ):
        assert client.post("/api/v1" + path, headers=auth, json=body).status_code == (
            201 if role == "admin" else 403
        )
    assert client.get("/api/v1/ssh-credentials", headers=auth).status_code == (
        200 if role == "admin" else 403
    )
    for path in ("/servers", "/ssh-credentials", "/alert-rules"):
        assert client.put(
            f"/api/v1{path}/{uuid4()}",
            headers=auth,
            json=server_body
            if path == "/servers"
            else (
                {"name": "secret", "auth_type": "password", "secret": "password"}
                if path == "/ssh-credentials"
                else {"code": "Test", "metric": "target_up", "threshold": 1}
            ),
        ).status_code == (404 if role == "admin" else 403)
        assert client.delete(f"/api/v1{path}/{uuid4()}", headers=auth).status_code == (
            404 if role == "admin" else 403
        )
    assert client.post(f"/api/v1/servers/{uuid4()}/test-ssh", headers=auth).status_code == (
        404 if role == "admin" else 403
    )
    assert client.post(f"/api/v1/servers/{uuid4()}/sync-containers", headers=auth).status_code == (
        403 if role == "viewer" else 404
    )
    assert client.post(
        f"/api/v1/alerts/{uuid4()}/ack", headers=auth, json={"comment": "checked"}
    ).status_code == (403 if role == "viewer" else 404)


@pytest.mark.parametrize(
    "path",
    [
        "/servers",
        "/containers",
        "/ssh-credentials",
        "/alerts",
        "/alert-rules",
        "/monitoring/targets",
    ],
)
def test_anonymous_resource_access(client, path):
    assert client.get("/api/v1" + path).status_code == 401


def test_credentials_encryption_rotation_and_redaction(client, admin_headers):
    secret = "top-secret-ssh-password-2026"
    body = {"name": "ssh", "auth_type": "password", "secret": secret}
    response = client.post("/api/v1/ssh-credentials", headers=admin_headers, json=body)
    assert response.status_code == 201
    credential_id = response.json()["id"]
    assert secret not in response.text
    assert "encrypted_secret" not in response.text
    with SessionLocal() as db:
        record = db.get(SSHCredential, credential_id)
        ciphertext = record.encrypted_secret
        assert secret not in ciphertext
        before = record.updated_at
    key = get_settings().credential_key_file.read_bytes().strip()
    assert secret in Fernet(key).decrypt(ciphertext.encode()).decode()
    body["secret"] = "replacement-ssh-password"
    assert (
        client.put(
            f"/api/v1/ssh-credentials/{credential_id}", headers=admin_headers, json=body
        ).status_code
        == 200
    )
    with SessionLocal() as db:
        record = db.get(SSHCredential, credential_id)
        assert record.encrypted_secret != ciphertext and record.updated_at > before
    assert (
        client.delete(f"/api/v1/ssh-credentials/{credential_id}", headers=admin_headers).status_code
        == 204
    )
    assert client.get("/api/v1/ssh-credentials", headers=admin_headers).json() == []
    body["secret"] = "-----BEGIN OPENSSH PRIVATE KEY-----\nsecret-secret\n"
    body["auth_type"] = "private_key"
    bad = client.post("/api/v1/ssh-credentials", headers=admin_headers, json=body)
    assert bad.status_code == 422 and "secret-secret" not in bad.text


def test_server_targets_soft_delete_and_discovery(client, admin_headers, server_id, server_body):
    groups = client.get("/api/v1/monitoring/targets", headers=SERVICE_HEADERS).json()
    assert len(groups) == 3
    assert {group["labels"]["server_id"] for group in groups} == {server_id}
    assert (
        client.post("/api/v1/servers", headers=admin_headers, json=server_body).status_code == 409
    )
    server_body["monitoring_enabled"] = False
    assert (
        client.put(
            f"/api/v1/servers/{server_id}", headers=admin_headers, json=server_body
        ).status_code
        == 200
    )
    assert client.get("/api/v1/monitoring/targets", headers=SERVICE_HEADERS).json() == []
    assert (
        client.post(
            f"/api/v1/servers/{server_id}/sync-containers", headers=admin_headers
        ).status_code
        == 409
    )
    assert (
        client.get(
            f"/api/v1/servers/{server_id}/collectors-compose", headers=admin_headers
        ).status_code
        == 200
    )
    assert client.delete(f"/api/v1/servers/{server_id}", headers=admin_headers).status_code == 204
    assert client.get(f"/api/v1/servers/{server_id}", headers=admin_headers).status_code == 404
    assert client.get("/api/v1/servers", headers=admin_headers).json() == []


@pytest.mark.parametrize(
    "field,value",
    [
        ("host", "host; whoami"),
        ("node_exporter_target", "localhost:0"),
        ("cadvisor_target", "http://example.com"),
        ("ssh_host_key", "invalid-key"),
        ("health_url", "file:///etc/passwd"),
        ("health_url", "http://user:secret@host"),
    ],
)
def test_server_validation(client, admin_headers, server_body, field, value):
    server_body[field] = value
    response = client.post("/api/v1/servers", headers=admin_headers, json=server_body)
    assert response.status_code == 422
    assert value not in response.text


def test_service_tokens_are_independent(client, admin_headers):
    for headers in ({}, admin_headers, {"Authorization": "Bearer wrong"}):
        assert (
            client.post(
                "/api/v1/hooks/alertmanager", headers=headers, json=webhook_body()
            ).status_code
            == 401
        )
        assert client.get("/api/v1/monitoring/targets", headers=headers).status_code == 401


def test_container_sync_is_idempotent_and_failure_preserves_assets(
    client,
    admin_headers,
    server_id,
    monkeypatch,
):
    timestamp = datetime.now(UTC).timestamp()
    item = {
        "metric": {"id": "/docker/test", "name": "test-app", "image": "test:1"},
        "value": [timestamp, str(timestamp)],
    }

    def query(path, params):
        return {
            "resultType": "vector",
            "result": (
                [{"value": [timestamp, "1"]}] if params["query"].startswith("up") else [item]
            ),
        }

    monkeypatch.setattr("app.services.resources.crud.prometheus", query)
    path = f"/api/v1/servers/{server_id}/sync-containers"
    first = client.post(path, headers=admin_headers)
    assert first.status_code == 200, first.text
    second = client.post(path, headers=admin_headers)
    assert first.json()[0]["id"] == second.json()[0]["id"]
    container_id = first.json()[0]["id"]

    def failed(*args, **kwargs):
        raise HTTPException(502, "Prometheus request failed")

    monkeypatch.setattr("app.services.resources.crud.prometheus", failed)
    assert client.post(path, headers=admin_headers).status_code == 502
    assert (
        client.get(f"/api/v1/containers/{container_id}", headers=admin_headers).json()["status"]
        == "running"
    )
    monkeypatch.setattr("app.services.resources.crud.prometheus", query)
    item["value"][1] = str(timestamp - 120)
    assert client.post(path, headers=admin_headers).json()[0]["status"] == "stopped"
    client.delete(f"/api/v1/servers/{server_id}", headers=admin_headers)
    assert client.get("/api/v1/containers", headers=admin_headers).json() == []
    with SessionLocal() as db:
        assert db.get(Container, container_id).deleted_at is not None


def test_metric_queries_are_scoped_and_ranges_bounded(
    client, admin_headers, server_id, monkeypatch
):
    calls = []

    def query(path, params):
        calls.append((path, params))
        return {"resultType": "matrix", "result": []}

    monkeypatch.setattr("app.services.resources.metrics.prometheus", query)
    path = f"/api/v1/servers/{server_id}/metrics"
    assert (
        client.get(path, headers=admin_headers, params={"metric": "host_cpu_percent"}).status_code
        == 200
    )
    assert server_id in calls[-1][1]["query"]
    now = datetime.now(UTC)
    params = {
        "metric": "host_cpu_percent",
        "start": (now - timedelta(hours=1)).isoformat(),
        "end": now.isoformat(),
        "step": 30,
    }
    assert client.get(path, headers=admin_headers, params=params).status_code == 200
    assert calls[-1][0] == "/api/v1/query_range"
    params["start"] = (now - timedelta(days=32)).isoformat()
    assert client.get(path, headers=admin_headers, params=params).status_code == 422
    params["start"] = "2026-01-01T00:00:00"
    assert client.get(path, headers=admin_headers, params=params).status_code == 422
    assert (
        client.get(path, headers=admin_headers, params={"metric": 'up{job="secret"}'}).status_code
        == 422
    )


def test_scheduled_inventory_detects_disappearance_and_preserves_failure(
    client, admin_headers, server_id, server_body, monkeypatch
):
    sample = {
        "metric": {"id": "/docker/worker", "name": "worker", "image": "worker:1"},
        "value": [0, str(datetime.now(UTC).timestamp())],
    }
    items = [sample]

    def query(path, params):
        return {"result": [{"value": [0, "1"]}] if params["query"].startswith("up") else items}

    monkeypatch.setattr("app.services.resources.crud.prometheus", query)
    path = "/api/v1/monitoring/container-metrics"
    assert client.get(path).status_code == 401
    assert client.get(path, headers=admin_headers).status_code == 401
    running = client.get(path, headers=SERVICE_HEADERS)
    assert running.status_code == 200 and 'name="worker"' in running.text
    container_id = client.get("/api/v1/containers", headers=admin_headers).json()[0]["id"]
    items.clear()
    stopped = client.get(path, headers=SERVICE_HEADERS).text
    assert "platform_container_up{" in stopped and 'image="worker:1"} 0' in stopped
    assert (
        client.get(f"/api/v1/containers/{container_id}", headers=admin_headers).json()["status"]
        == "stopped"
    )

    def unavailable(*args, **kwargs):
        raise HTTPException(502, "Unavailable")

    monkeypatch.setattr("app.services.resources.crud.prometheus", unavailable)
    unknown = client.get(path, headers=SERVICE_HEADERS).text
    assert f'platform_inventory_up{{server_id="{server_id}",source_epoch=' in unknown
    assert unknown.splitlines()[2].endswith(" 0")
    assert 'image="worker:1"} 0' in unknown
    monkeypatch.setattr("app.services.resources.crud.prometheus", query)
    items.append(sample)
    assert 'image="worker:1"} 1' in client.get(path, headers=SERVICE_HEADERS).text
    assert (
        client.get(f"/api/v1/containers/{container_id}", headers=admin_headers).json()["status"]
        == "running"
    )
    assert (
        client.delete(f"/api/v1/containers/{container_id}", headers=admin_headers).status_code
        == 204
    )
    assert "platform_container_up{" not in client.get(path, headers=SERVICE_HEADERS).text
    server_body["monitoring_enabled"] = False
    client.put(f"/api/v1/servers/{server_id}", headers=admin_headers, json=server_body)
    assert "platform_inventory_up{" not in client.get(path, headers=SERVICE_HEADERS).text


@pytest.mark.parametrize("status", ["firing", "resolved"])
def test_acceptance_email_rejects_historical_and_other_occurrences(status):
    from scripts.accept_monitoring import email_for_event

    event = {
        "starts_at": "2026-10-02T00:00:00Z",
        "server_id": "server-a",
        "labels": {"alertname": "LocalWorkerDown", "container_id": "worker-a"},
    }
    start = int(datetime.fromisoformat(event["starts_at"]).timestamp())
    messages = [{"ID": "old", "Subject": f"{status.upper()} LocalWorkerDown"}]
    body = f"server-a worker-a event-start={start}"

    def fetch(id):
        return {"HTML": body}

    assert email_for_event(messages, {"old"}, event, status, fetch) is None
    messages[0]["ID"] = "new"
    assert (
        email_for_event(
            messages,
            set(),
            event,
            status,
            lambda id: {"HTML": body.replace(str(start), str(start - 60))},
        )
        is None
    )
    assert (
        email_for_event(
            messages,
            set(),
            event,
            status,
            lambda id: {"HTML": body.replace("worker-a", "worker-b")},
        )
        is None
    )
    assert email_for_event(messages, set(), event, status, fetch)["id"] == "new"


def test_rule_apply_and_reload_failure_restore_file(client, admin_headers, server_id, monkeypatch):
    body = {
        "code": "HostMemoryHigh",
        "metric": "host_memory_percent",
        "threshold": 85,
        "server_id": server_id,
    }
    rule = client.post("/api/v1/alert-rules", headers=admin_headers, json=body)
    assert rule.status_code == 201
    runtime = {"groups": []}

    def working(path, *args, **kwargs):
        if path == "/-/reload":
            source = get_settings().monitoring_rules_dir / "platform.yml"
            rules = yaml.safe_load(source.read_text())["groups"][0]["rules"]
            runtime["groups"] = [
                {
                    "name": "platform",
                    "rules": [
                        {
                            "name": r["alert"],
                            "query": r["expr"],
                            "duration": int(r["for"][:-1]),
                            "labels": r["labels"],
                            "annotations": r["annotations"],
                        }
                        for r in rules
                    ],
                }
            ]
            return {}
        return runtime

    monkeypatch.setattr("app.services.monitoring.config.prometheus", working)
    assert client.post("/api/v1/monitoring/apply-rules", headers=admin_headers).json() == {
        "applied": 1
    }
    path = get_settings().monitoring_rules_dir / "platform.yml"
    previous = path.read_bytes()
    rendered = yaml.safe_load(previous)["groups"][0]["rules"][0]
    assert server_id in rendered["expr"] and rendered["for"] == "30s"
    assert rendered["labels"]["rule_id"] == rule.json()["id"]
    client.delete(f"/api/v1/alert-rules/{rule.json()['id']}", headers=admin_headers)

    def failed(*args, **kwargs):
        raise HTTPException(502, "Unavailable")

    monkeypatch.setattr("app.services.monitoring.config.prometheus", failed)
    failed_response = client.post("/api/v1/monitoring/apply-rules", headers=admin_headers)
    assert failed_response.status_code == 502
    assert "unknown" in failed_response.json()["detail"]
    assert path.read_bytes() == previous
    reloads = []

    def lost_response(path, *args, **kwargs):
        result = working(path, *args, **kwargs)
        if path == "/-/reload":
            reloads.append(runtime["groups"])
            if len(reloads) == 1:
                raise HTTPException(502, "Response lost after successful reload")
        return result

    monkeypatch.setattr("app.services.monitoring.config.prometheus", lost_response)
    failure = client.post("/api/v1/monitoring/apply-rules", headers=admin_headers)
    assert failure.status_code == 502
    assert "active rules restored" in failure.json()["detail"]
    assert reloads[0][0]["rules"] == []
    assert reloads[1][0]["rules"][0]["name"] == body["code"]
    assert path.read_bytes() == previous
    monkeypatch.setattr("app.services.monitoring.config.prometheus", working)
    assert client.post("/api/v1/monitoring/apply-rules", headers=admin_headers).json() == {
        "applied": 0
    }


def test_active_rule_verification_accepts_prometheus_legacy_normalization(monkeypatch):
    from app.services.monitoring.config import verify_active_rules

    expected = [
        {
            "alert": "Legacy",
            "expr": '(up{server_id="abc",job="node"}) < 1.0',
            "for": "15s",
            "labels": {},
            "annotations": {},
        }
    ]
    actual = {
        "groups": [
            {
                "name": "platform",
                "rules": [
                    {
                        "name": "Legacy",
                        "query": '(up{job="node",server_id="abc"}) < 1',
                        "duration": 15,
                        "labels": {},
                        "annotations": {},
                    }
                ],
            }
        ]
    }
    monkeypatch.setattr("app.services.monitoring.config.prometheus", lambda *args, **kwargs: actual)
    verify_active_rules(expected)
    actual["groups"][0]["rules"][0]["query"] = '(up{job="node",server_id="different"}) < 1'
    with pytest.raises(HTTPException):
        verify_active_rules(expected)


def test_alert_duplicate_recovery_late_firing_and_recurrence(client, admin_headers, server_id):
    body = webhook_body()
    body["alerts"][0]["labels"]["server_id"] = server_id
    path = "/api/v1/hooks/alertmanager"
    assert client.post(path, headers=SERVICE_HEADERS, json=body).json()["changed"] == 1
    assert client.post(path, headers=SERVICE_HEADERS, json=body).json()["changed"] == 0
    event = client.get("/api/v1/alerts", headers=admin_headers).json()[0]
    assert event["server_id"] == server_id and event["status"] == "firing"
    assert (
        client.post(
            f"/api/v1/alerts/{event['id']}/ack",
            headers=admin_headers,
            json={"comment": "Investigating"},
        ).status_code
        == 200
    )
    resolved = webhook_body("resolved", datetime.fromisoformat(body["alerts"][0]["startsAt"]))
    assert client.post(path, headers=SERVICE_HEADERS, json=resolved).json()["changed"] == 1
    assert client.post(path, headers=SERVICE_HEADERS, json=resolved).json()["changed"] == 0
    assert client.post(path, headers=SERVICE_HEADERS, json=body).json()["changed"] == 0
    events = client.get("/api/v1/alerts", headers=admin_headers).json()
    assert len(events) == 1 and events[0]["status"] == "resolved"
    assert events[0]["comment"] == "Investigating"
    assert client.post(path, headers=SERVICE_HEADERS, json=webhook_body()).json()["changed"] == 1
    assert len(client.get("/api/v1/alerts", headers=admin_headers).json()) == 2


def test_recovery_before_firing_stays_resolved(client, admin_headers):
    start = datetime.now(UTC) - timedelta(minutes=2)
    path = "/api/v1/hooks/alertmanager"
    client.post(path, headers=SERVICE_HEADERS, json=webhook_body("resolved", start))
    client.post(path, headers=SERVICE_HEADERS, json=webhook_body("firing", start))
    events = client.get("/api/v1/alerts", headers=admin_headers).json()
    assert len(events) == 1 and events[0]["status"] == "resolved"


def test_concurrent_duplicate_webhooks_create_once(client):
    body = webhook_body()
    barrier = Barrier(2)

    def send():
        barrier.wait(timeout=5)
        return client.post("/api/v1/hooks/alertmanager", headers=SERVICE_HEADERS, json=body)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: send(), range(2)))
    assert all(result.status_code == 200 for result in results)
    assert sum(result.json()["changed"] for result in results) == 1
    with SessionLocal() as db:
        assert len(db.scalars(select(AlertEvent)).all()) == 1


@pytest.fixture
def ssh_service():
    key = paramiko.RSAKey.generate(2048)
    user_key = paramiko.RSAKey.generate(2048)
    stop = Event()
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    listener.settimeout(0.2)

    class AuthServer(paramiko.ServerInterface):
        def check_auth_password(self, username, password):
            return (
                paramiko.AUTH_SUCCESSFUL
                if username == "ops" and password == "ssh-test-password"
                else paramiko.AUTH_FAILED
            )

        def check_auth_publickey(self, username, offered):
            return (
                paramiko.AUTH_SUCCESSFUL
                if username == "ops" and offered == user_key
                else paramiko.AUTH_FAILED
            )

        def get_allowed_auths(self, username):
            return "password,publickey"

    def serve():
        while not stop.is_set():
            try:
                conn, _ = listener.accept()
            except TimeoutError:
                continue
            with paramiko.Transport(conn) as transport:
                transport.add_server_key(key)
                try:
                    transport.start_server(server=AuthServer())
                    while transport.is_active() and not stop.wait(0.02):
                        pass
                except (paramiko.SSHException, EOFError):
                    pass

    thread = Thread(target=serve, daemon=True)
    thread.start()
    yield listener.getsockname()[1], key, user_key
    stop.set()
    thread.join(timeout=3)
    listener.close()


@pytest.mark.parametrize("mode", ["password", "private_key", "wrong_password", "wrong_host_key"])
def test_real_ssh_protocol_pin_and_auth(client, admin_headers, server_body, ssh_service, mode):
    port, host_key, user_key = ssh_service
    secret = "ssh-test-password"
    auth_type = "password"
    if mode == "private_key":
        output = io.StringIO()
        user_key.write_private_key(output)
        secret = output.getvalue()
        auth_type = "private_key"
    if mode == "wrong_password":
        secret = "incorrect-password"
    if mode == "wrong_host_key":
        host_key = paramiko.RSAKey.generate(2048)
    credential = client.post(
        "/api/v1/ssh-credentials",
        headers=admin_headers,
        json={"name": "ssh-test", "auth_type": auth_type, "secret": secret},
    )
    assert credential.status_code == 201, credential.text
    credential_id = credential.json()["id"]
    server_body.update(
        {
            "credential_id": credential_id,
            "ssh_port": port,
            "ssh_host_key": f"{host_key.get_name()} {host_key.get_base64()}",
        }
    )
    response = client.post("/api/v1/servers", headers=admin_headers, json=server_body)
    assert response.status_code == 201
    server_id = response.json()["id"]
    assert (
        client.delete(f"/api/v1/ssh-credentials/{credential_id}", headers=admin_headers).status_code
        == 409
    )
    result = client.post(f"/api/v1/servers/{server_id}/test-ssh", headers=admin_headers)
    assert result.status_code == 200, result.text
    expected = {
        "password": "ok",
        "private_key": "ok",
        "wrong_password": "authentication_failed",
        "wrong_host_key": "host_key_mismatch",
    }[mode]
    assert result.json()["status"] == expected
    assert result.json()["success"] == (expected == "ok")
    assert secret not in result.text
    assert (
        client.get(f"/api/v1/servers/{server_id}", headers=admin_headers).json()[
            "last_ssh_test_status"
        ]
        == expected
    )


def test_staged_migration_roundtrip(migrated_database):
    command.downgrade(migrated_database, "0003_resources")
    command.upgrade(migrated_database, "head")
    command.check(migrated_database)
