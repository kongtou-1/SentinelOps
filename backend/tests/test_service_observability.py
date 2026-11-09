import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from uuid import UUID

import pytest
import yaml
from sqlalchemy import select, text
from test_ai import ai_config as ai_config
from test_incidents import alert, get_incidents, post
from test_resources_alerts import SERVICE_HEADERS
from test_resources_alerts import isolated_secrets as isolated_secrets
from test_service_token_auth import configuration
from test_service_token_auth import token_target as token_target

from app.db.session import SessionLocal
from app.integrations.service_probes import module_name
from app.models import (
    AgentAnalysis,
    AlertEvent,
    Application,
    Container,
    LLMEndpoint,
    LLMServiceBinding,
    Server,
    ServiceMonitor,
)
from app.services.monitoring.recovery import evidence, notification_snapshot, reconcile


@pytest.fixture
def target():
    state = {"code": 200, "body": {"ok": True}, "delay": 0, "requests": [], "blackbox_success": 1}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.reply()

        def do_POST(self):
            self.reply()

        def reply(self):
            raw = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            state["requests"].append(
                {"headers": dict(self.headers), "body": raw, "path": self.path}
            )
            time.sleep(state["delay"])
            if self.path.startswith("/probe?"):
                payload = (
                    f"probe_success {state['blackbox_success']}\nprobe_duration_seconds 0.012\n"
                ).encode()
            else:
                payload = json.dumps(state["body"]).encode()
            self.send_response(state["code"])
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            try:
                self.wfile.write(payload)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"127.0.0.1:{server.server_port}", state
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)


@pytest.fixture
def application(client, admin_headers):
    response = client.post(
        "/api/v1/applications",
        headers=admin_headers,
        json={"code": "service-test", "name": "问答服务"},
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def monitor(client, auth, application, address, **extra):
    payload = {
        "name": "关键问答 API",
        "target": "http://" + address + "/work",
        "route": "/work",
        "assertions": {"ok": True},
        **extra,
    }
    response = client.post(
        f"/api/v1/business-services/{application}/monitors", headers=auth, json=payload
    )
    assert response.status_code == 201, response.text
    return response.json(), payload


def run(client, headers, identifier):
    response = client.post(f"/api/v1/service-monitors/{identifier}/probe", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def test_token_auth_draft_then_save_reuse_update_and_clear(
    client, admin_headers, application, token_target
):
    url, state = token_target
    payload = configuration(url).model_dump(exclude_none=True)
    tested = client.post("/api/v1/service-monitors/test", headers=admin_headers, json=payload)
    assert tested.status_code == 200, tested.text
    assert tested.json()["success"] and tested.json()["refresh_tested"]
    assert "private" not in tested.text
    with SessionLocal() as db:
        assert db.scalar(select(ServiceMonitor)) is None
    saved = client.post(
        f"/api/v1/business-services/{application}/monitors", headers=admin_headers, json=payload
    )
    assert saved.status_code == 201, saved.text
    row = saved.json()
    assert row["token_auth_configured"] and "private" not in saved.text
    with SessionLocal() as db:
        assert "private-password" not in db.get(ServiceMonitor, UUID(row["id"])).encrypted_request
    state["requests"].clear()
    assert run(client, admin_headers, row["id"])["status"] == "healthy"
    assert run(client, admin_headers, row["id"])["status"] == "healthy"
    assert [p for p, _, _ in state["requests"]].count("/login") == 1
    payload.pop("token_auth")
    updated = client.put(
        f"/api/v1/service-monitors/{row['id']}", headers=admin_headers, json=payload
    )
    assert updated.status_code == 200 and updated.json()["token_auth_configured"]
    assert run(client, admin_headers, row["id"])["status"] == "healthy"
    assert [p for p, _, _ in state["requests"]].count("/login") == 2
    before = updated.json()
    draft = client.post(
        "/api/v1/service-monitors/test",
        headers=admin_headers,
        json={**payload, "monitor_id": row["id"]},
    )
    assert draft.json()["success"], draft.text
    after = client.get(f"/api/v1/service-monitors/{row['id']}", headers=admin_headers).json()
    assert before["config_changed_at"] == after["config_changed_at"]
    assert after["last_probe_success"] is True
    cleared = client.put(
        f"/api/v1/service-monitors/{row['id']}",
        headers=admin_headers,
        json={**payload, "token_auth": False},
    )
    assert cleared.status_code == 200
    assert not cleared.json()["token_auth_configured"]
    assert not client.app.state.service_probes.token_sessions


@pytest.mark.parametrize("role", ["viewer", "operator"])
def test_token_draft_requires_manage_permission(client, make_user, login, token_target, role):
    make_user("token-" + role, role)
    headers = {"Authorization": "Bearer " + login("token-" + role)["access_token"]}
    response = client.post(
        "/api/v1/service-monitors/test",
        headers=headers,
        json=configuration(token_target[0]).model_dump(),
    )
    assert response.status_code == 403
    assert not token_target[1]["requests"]


@pytest.mark.parametrize(
    "code,body,expected",
    [
        (200, {"ok": True}, "healthy"),
        (200, {"ok": False}, "failed"),
        (500, {"ok": True}, "failed"),
        (302, {"ok": True}, "failed"),
    ],
)
def test_http_status_and_business_assertions_are_independent_of_container_state(
    client, admin_headers, application, target, code, body, expected
):
    address, state = target
    row, _ = monitor(client, admin_headers, application, address)
    state.update(code=code, body=body)
    result = run(client, admin_headers, row["id"])
    assert result["status"] == expected
    assert state["requests"][0]["headers"]["X-SentinelOps-Probe"] == "1"
    view = client.get(
        f"/api/v1/business-services/{application}/observability", headers=admin_headers
    ).json()
    assert view["business_status"] == expected and view["containers"] == []
    assert (
        "response_sha256" in result["last_evidence"]
        if code == 200
        else result["last_error_code"] == "unexpected_status"
    )


def test_encrypted_headers_body_and_version_reset(client, admin_headers, application, target):
    address, state = target
    secret = "probe-private-test-value"
    row, payload = monitor(
        client,
        admin_headers,
        application,
        address,
        method="POST",
        safe_post=True,
        headers={"Authorization": "Bearer " + secret},
        request_body={"password": secret},
    )
    result = run(client, admin_headers, row["id"])
    assert result["status"] == "healthy" and secret not in json.dumps(result)
    assert secret in state["requests"][0]["body"].decode()
    with SessionLocal() as db:
        stored = db.get(ServiceMonitor, UUID(row["id"]))
        assert secret not in stored.encrypted_request
    payload.pop("headers")
    payload.pop("request_body")
    response = client.put(
        f"/api/v1/service-monitors/{row['id']}", headers=admin_headers, json=payload
    )
    assert response.status_code == 200, response.text
    updated = response.json()
    assert updated["config_changed_at"] != row["config_changed_at"]
    assert updated["last_probe_success"] is None and updated["last_success_at"] is None
    assert updated["request_configured"]


@pytest.mark.parametrize(
    "change",
    [
        {"target": "http://user:secret@localhost"},
        {"target": "http://localhost?api_key=secret"},
        {"method": "POST"},
        {"headers": {"Host": "other"}},
        {"headers": {"Authorization": "bad\nvalue"}},
        {"protocol": "tcp", "target": "host:70000", "assertions": {}, "route": ""},
    ],
)
def test_invalid_probe_configuration_is_rejected_without_echoing_credentials(
    client, admin_headers, application, target, change
):
    payload = {"name": "unsafe", "target": "http://" + target[0], **change}
    response = client.post(
        f"/api/v1/business-services/{application}/monitors", headers=admin_headers, json=payload
    )
    assert response.status_code == 422, response.text
    assert "secret" not in response.text


def test_protocol_change_requires_explicit_encrypted_config_clear(
    client, admin_headers, application, target
):
    row, _ = monitor(
        client, admin_headers, application, target[0], headers={"Authorization": "Bearer private"}
    )
    payload = {"name": "TCP", "protocol": "tcp", "target": target[0]}
    path = f"/api/v1/service-monitors/{row['id']}"
    rejected = client.put(path, headers=admin_headers, json=payload)
    assert rejected.status_code == 422
    with SessionLocal() as db:
        assert db.get(ServiceMonitor, UUID(row["id"])).protocol == "http"
    saved = client.put(
        path, headers=admin_headers, json={**payload, "headers": {}, "request_body": {}}
    )
    assert saved.status_code == 200, saved.text
    assert not saved.json()["request_configured"]
    assert run(client, admin_headers, row["id"])["status"] == "healthy"


@pytest.mark.parametrize("role", ["viewer", "operator", "admin"])
def test_permissions_and_metrics_service_token(
    client, make_user, login, application, target, admin_headers, role
):
    username = "probe-" + role
    make_user(username, role)
    headers = {"Authorization": "Bearer " + login(username)["access_token"]}
    row, payload = monitor(client, admin_headers, application, target[0])
    assert client.get(f"/api/v1/service-monitors/{row['id']}", headers=headers).status_code == 200
    assert client.put(
        f"/api/v1/service-monitors/{row['id']}", headers=headers, json=payload
    ).status_code == (200 if role == "admin" else 403)
    assert client.post(
        f"/api/v1/service-monitors/{row['id']}/probe", headers=headers
    ).status_code == (403 if role == "viewer" else 200)
    assert (
        client.get("/api/v1/monitoring/service-probe-metrics", headers=headers).status_code == 401
    )
    assert (
        client.get("/api/v1/monitoring/service-probe-metrics", headers=SERVICE_HEADERS).status_code
        == 200
    )


@pytest.mark.parametrize("protocol", ["tcp", "dns"])
def test_platform_tcp_and_dns_use_real_local_network(
    client, admin_headers, application, target, protocol
):
    row, _ = monitor(
        client,
        admin_headers,
        application,
        target[0],
        protocol=protocol,
        target=target[0] if protocol == "tcp" else "localhost",
        route="",
        assertions={},
    )
    assert run(client, admin_headers, row["id"])["status"] == "healthy"


def test_host_blackbox_failure_is_unknown_and_config_is_version_bound(
    client, admin_headers, application, target
):
    address, state = target
    with SessionLocal() as db:
        server = Server(
            name="probe-host",
            host="127.0.0.1",
            node_exporter_target="host:9100",
            cadvisor_target="host:8080",
            blackbox_exporter_target=address,
        )
        db.add(server)
        db.commit()
        server_id = str(server.id)
    row, payload = monitor(
        client, admin_headers, application, address, probe_server_id=server_id, assertions={}
    )
    assert run(client, admin_headers, row["id"])["status"] == "healthy"
    content = client.get(f"/api/v1/servers/{server_id}/probe-config", headers=admin_headers)
    modules = yaml.safe_load(content.text)["modules"]
    assert len(modules) == 1
    first = next(iter(modules))
    with SessionLocal() as db:
        assert first == module_name(db.get(ServiceMonitor, UUID(row["id"])))
    state["code"] = 503
    result = run(client, admin_headers, row["id"])
    assert result["status"] == "unknown" and result["last_probe_success"] is None
    assert result["last_failure_at"] is None
    client.put(f"/api/v1/service-monitors/{row['id']}", headers=admin_headers, json=payload)
    updated = yaml.safe_load(
        client.get(f"/api/v1/servers/{server_id}/probe-config", headers=admin_headers).text
    )["modules"]
    assert first not in updated


def test_real_traffic_has_explicit_unconfigured_and_excludes_probes(
    client, admin_headers, application, target, monkeypatch
):
    row, _ = monitor(client, admin_headers, application, target[0])
    path = f"/api/v1/service-monitors/{row['id']}/metrics?metric=service_latency_p95_seconds"
    assert client.get(path, headers=admin_headers).json()["availability"] == "unconfigured"
    client.put(
        f"/api/v1/business-services/{application}/traffic",
        headers=admin_headers,
        json={"traffic_target": target[0]},
    )
    captured = []

    def query(path, params):
        captured.append(params["query"])
        return {"resultType": "vector", "result": []}

    monkeypatch.setattr("app.integrations.prometheus.prometheus", query)
    assert client.get(path, headers=admin_headers).json()["availability"] == "unavailable"
    assert 'traffic="business"' in captured[0] and "histogram_quantile" in captured[0]
    assert "service_probe_duration" not in captured[0]
    groups = client.get("/api/v1/monitoring/targets", headers=SERVICE_HEADERS).json()
    assert any(
        g["labels"].get("collector") == "service-traffic"
        and g["labels"].get("application_id") == application
        for g in groups
    )


def service_alert(client, headers, monitor_row):
    response = client.post(
        "/api/v1/alert-rules",
        headers=headers,
        json={
            "code": "ServiceDown",
            "metric": "service_probe_up",
            "threshold": 1,
            "service_monitor_id": monitor_row["id"],
            "auto_diagnose": True,
        },
    )
    assert response.status_code == 201, response.text
    rule = response.json()
    from app.models import AlertRule
    from app.services.monitoring.config import rule_config

    with SessionLocal() as db:
        snapshot = {
            **rule_config(db.get(AlertRule, UUID(rule["id"]))),
            "source_epoch": db.get(
                ServiceMonitor, UUID(monitor_row["id"])
            ).config_changed_at.isoformat(),
        }
    item = alert(
        monitor_id=monitor_row["id"],
        rule_id=rule["id"],
        application_id=monitor_row["application_id"],
    )
    item["annotations"]["platform_rule_snapshot"] = json.dumps(snapshot)
    post(client, item)
    return item, snapshot


def test_service_alert_auto_diagnosis_and_fresh_recovery(
    client, admin_headers, application, target, ai_config, monkeypatch
):
    address, state = target
    row, _ = monitor(client, admin_headers, application, address)
    state["body"] = {"ok": False}
    run(client, admin_headers, row["id"])
    item, snapshot = service_alert(client, admin_headers, row)
    assert notification_snapshot(item["annotations"]) == snapshot
    with SessionLocal() as db:
        assert db.scalar(select(AgentAnalysis)).trigger == "automatic"
    ended = datetime.now(UTC)
    item.update(status="resolved", endsAt=ended.isoformat())
    post(client, item)
    with SessionLocal() as db:
        event = db.scalar(select(AlertEvent))
        assert evidence(db, event, datetime.now(UTC))[0] == "pending"
    state["body"] = {"ok": True}
    run(client, admin_headers, row["id"])
    monkeypatch.setattr(
        "app.services.monitoring.recovery.query",
        lambda *args: [{"metric": {}, "value": [time.time(), "1"]}],
    )
    with SessionLocal() as db:
        assert reconcile(db) == 1
    incident = get_incidents(client, admin_headers)[0]
    assert incident["status"] == "recovered"
    view = client.get(
        f"/api/v1/business-services/{application}/observability", headers=admin_headers
    )
    assert view.status_code == 200, view.text
    assert len(view.json()["alerts"]) == 1 and len(view.json()["incidents"]) == 1


def test_old_config_cannot_supply_recovery_evidence(
    client, admin_headers, application, target, ai_config
):
    row, payload = monitor(client, admin_headers, application, target[0])
    item, _ = service_alert(client, admin_headers, row)
    item.update(status="resolved", endsAt=datetime.now(UTC).isoformat())
    post(client, item)
    payload["assertions"] = {"version": "2"}
    client.put(f"/api/v1/service-monitors/{row['id']}", headers=admin_headers, json=payload)
    with SessionLocal() as db:
        assert evidence(db, db.scalar(select(AlertEvent)), datetime.now(UTC))[:2] == (
            "terminated",
            "collection_changed",
        )


def test_dependency_and_container_links_do_not_attribute_unrelated_host_alerts(
    client, admin_headers, application, target, monkeypatch
):
    monkeypatch.setattr("app.services.resources.overview.fetch", lambda *_: None)
    with SessionLocal() as db:
        dependency = Application(code="dependency", name="检索")
        server = Server(
            name="shared",
            host="127.0.0.1",
            node_exporter_target="host:9100",
            cadvisor_target="host:8080",
        )
        db.add_all([dependency, server])
        db.flush()
        app = db.get(Application, UUID(application))
        app.depends_on = [str(dependency.id)]
        container = Container(
            application_id=app.id,
            server_id=server.id,
            docker_id="a" * 64,
            name="worker",
            image="test",
            status="running",
            labels={},
            last_seen_at=datetime.now(UTC),
        )
        db.add(container)
        db.commit()
        dependency_id = str(dependency.id)
        server_id = str(server.id)
        container_id = str(container.id)
    row, _ = monitor(client, admin_headers, application, target[0], container_id=container_id)
    post(client, alert(server_id=server_id))
    view = client.get(
        f"/api/v1/business-services/{application}/observability", headers=admin_headers
    )
    assert view.status_code == 200, view.text
    data = view.json()
    assert data["containers"][0]["id"] == container_id
    assert data["dependencies"][0]["id"] == dependency_id
    assert data["alerts"] == []


def test_probe_timeout_and_same_target_concurrency(client, admin_headers, application, target):
    address, state = target
    row, _ = monitor(client, admin_headers, application, address, timeout_seconds=1)
    state["delay"] = 2
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(
            client.post, f"/api/v1/service-monitors/{row['id']}/probe", headers=admin_headers
        )
        deadline = time.monotonic() + 2
        while not state["requests"] and time.monotonic() < deadline:
            time.sleep(0.01)
        second = client.post(f"/api/v1/service-monitors/{row['id']}/probe", headers=admin_headers)
        assert second.status_code == 409
        result = first.result()
    assert result.status_code == 200 and result.json()["last_error_code"] == "timeout"


def test_llm_binding_direct_update_uses_database_trigger(client, admin_headers, application):
    with SessionLocal() as db:
        endpoint = LLMEndpoint(
            name="timestamp-target",
            base_url="https://example.invalid",
            model="test",
            encrypted_api_key="encrypted",
        )
        db.add(endpoint)
        db.flush()
        binding = LLMServiceBinding(
            application_id=UUID(application), llm_endpoint_id=endpoint.id, purpose="old"
        )
        db.add(binding)
        db.commit()
        before = binding.updated_at
        after = db.scalar(
            text("UPDATE llm_service_bindings SET purpose='new' WHERE id=:id RETURNING updated_at"),
            {"id": binding.id},
        )
        assert after > before


def test_diagnosis_receives_service_evidence_and_rejects_unrelated_monitor(
    client, admin_headers, application, target, ai_config, monkeypatch
):
    from app.services.diagnosis.tools import DiagnosticTools

    row, _ = monitor(client, admin_headers, application, target[0])
    service_alert(client, admin_headers, row)
    other, _ = monitor(client, admin_headers, application, target[0], name="另一个监测")
    with SessionLocal() as db:
        analysis = db.scalar(select(AgentAnalysis))
        analysis.status = "running"
        db.commit()
        identifier = analysis.id
    tools = DiagnosticTools(identifier)
    context = tools.call("get_alert_context", {}, internal=True)
    assert context["data"]["service_monitoring"][0]["monitor"]["id"] == row["id"]
    monkeypatch.setattr(
        "app.integrations.prometheus.prometheus", lambda *args, **kwargs: {"result": []}
    )
    result = tools.call(
        "get_service_metrics", {"asset_id": row["id"], "metric": "service_probe_up"}
    )
    assert result["data"]["availability"] == "unavailable"
    with pytest.raises(ValueError, match="outside incident scope"):
        tools.call("get_service_metrics", {"asset_id": other["id"], "metric": "service_probe_up"})


def test_mount_collector_keeps_missing_inventory_distinct_from_empty_inventory():
    from app.integrations.docker_state import Collector

    collector = Collector()
    collector.healthy = True
    collector.updated_at = time.time()
    identifier = "a" * 64
    collector.containers = {identifier: {"Names": ["/worker"], "State": "running"}}
    assert "docker_container_mount_inventory" not in collector.metrics()
    collector.containers[identifier]["Mounts"] = []
    assert "docker_container_mount_inventory" in collector.metrics()
    collector.containers[identifier]["Mounts"] = [
        {"Source": "/data/models", "Destination": "/models", "Type": "bind", "RW": False}
    ]
    output = collector.metrics()
    assert (
        'source="/data/models"' in output
        and 'destination="/models"' in output
        and 'read_only="true"' in output
    )
    collector.containers[identifier]["Mounts"] = [{"Source": "/data/models"}]
    assert "docker_container_mount_inventory" not in collector.metrics()
