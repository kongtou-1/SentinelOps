import asyncio
import json
import socket
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Event, Thread
from uuid import UUID, uuid4

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import select, text

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.models import LLMEndpoint
from app.schemas.llm import ProbeResult
from app.services.llm.probes import ProbeService
from app.services.monitoring.metrics import llm_metric_expression

KEY = "dummy-key-used-only-in-local-tests"
TOKEN = "monitoring-test-token-independent-of-jwt"
SERVICE_HEADERS = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture(autouse=True)
def llm_secrets(tmp_path, monkeypatch):
    for field, value in (
        ("credential_key_file", Fernet.generate_key().decode()),
        ("monitoring_token_file", TOKEN),
        ("alertmanager_token_file", TOKEN),
    ):
        path = tmp_path / field
        path.write_text(value, encoding="utf-8")
        monkeypatch.setattr(get_settings(), field, path)
    monkeypatch.setattr(get_settings(), "monitoring_rules_dir", tmp_path / "rules")


@pytest.fixture
def provider():
    state = {
        "status": 200,
        "content": " OK ",
        "requests": [],
        "entered": Event(),
        "release": Event(),
    }

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            state["requests"].append((self.path, self.headers["Authorization"], body))
            state["entered"].set()
            if state.get("blocked"):
                state["release"].wait(5)
            status = state["status"]
            self.send_response(status)
            if status == 302:
                self.send_header("Location", "/redirect-target")
            self.end_headers()
            payload = (
                state.get(
                    "payload",
                    {
                        "choices": [{"message": {"content": state["content"]}}],
                        "usage": {"prompt_tokens": 7, "completion_tokens": 1},
                    },
                )
                if status == 200
                else {"error": {"message": KEY}}
            )
            try:
                raw = json.dumps(payload).encode()
                if state.get("drip"):
                    for byte in raw:
                        self.wfile.write(bytes([byte]))
                        self.wfile.flush()
                        time.sleep(0.05)
                else:
                    self.wfile.write(raw)
            except (ConnectionResetError, BrokenPipeError):
                pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    state["base_url"] = f"http://127.0.0.1:{server.server_port}"
    yield state
    state["release"].set()
    server.shutdown()
    server.server_close()
    thread.join(2)


@pytest.fixture
def body(provider):
    return {
        "name": "local-llm",
        "base_url": provider["base_url"],
        "model": "configured-model",
        "api_key": KEY,
    }


@pytest.fixture
def endpoint_id(client, admin_headers, body):
    response = client.post("/api/v1/llm-endpoints", json=body, headers=admin_headers)
    assert response.status_code == 201, response.text
    return response.json()["id"]


@pytest.mark.parametrize("role", ["viewer", "operator", "admin"])
def test_permissions(client, make_user, login, body, role, monkeypatch):
    make_user(role, role)
    headers = {"Authorization": f"Bearer {login(role)['access_token']}"}
    assert client.get("/api/v1/llm-endpoints", headers=headers).status_code == 200
    assert client.post("/api/v1/llm-endpoints", headers=headers, json=body).status_code == (
        201 if role == "admin" else 403
    )
    for method in ("put", "delete"):
        kwargs = {"headers": headers}
        if method == "put":
            kwargs["json"] = body
        assert getattr(client, method)(
            f"/api/v1/llm-endpoints/{uuid4()}", **kwargs
        ).status_code == (404 if role == "admin" else 403)
    assert client.post(f"/api/v1/llm-endpoints/{uuid4()}/probe", headers=headers).status_code == (
        403 if role == "viewer" else 404
    )
    assert client.get("/api/v1/monitoring/llm-metrics", headers=headers).status_code == 401


def test_crud_encryption_key_rotation_and_soft_delete(client, admin_headers, body, endpoint_id):
    url = f"/api/v1/llm-endpoints/{endpoint_id}"
    response = client.get(url, headers=admin_headers)
    assert KEY not in response.text and "encrypted_api_key" not in response.text
    assert response.json()["status"] == "unknown"
    assert client.post("/api/v1/llm-endpoints", headers=admin_headers, json=body).status_code == 409
    with SessionLocal() as db:
        endpoint = db.get(LLMEndpoint, UUID(endpoint_id))
        original = endpoint.encrypted_api_key
        assert KEY not in original
        assert (
            Fernet(get_settings().credential_key_file.read_bytes())
            .decrypt(original.encode())
            .decode()
            == KEY
        )
        before = endpoint.updated_at
    replacement = {k: v for k, v in body.items() if k != "api_key"}
    replacement["name"] = "renamed"
    assert client.put(url, headers=admin_headers, json=replacement).status_code == 200
    with SessionLocal() as db:
        endpoint = db.get(LLMEndpoint, UUID(endpoint_id))
        assert endpoint.encrypted_api_key == original and endpoint.updated_at > before
    replacement["api_key"] = "rotated-local-test-key"
    assert client.put(url, headers=admin_headers, json=replacement).status_code == 200
    with SessionLocal() as db:
        assert db.get(LLMEndpoint, UUID(endpoint_id)).encrypted_api_key != original
    assert client.delete(url, headers=admin_headers).status_code == 204
    assert client.get(url, headers=admin_headers).status_code == 404
    assert client.post(url + "/probe", headers=admin_headers).status_code == 404
    assert client.get("/api/v1/llm-endpoints", headers=admin_headers).json() == []
    assert (
        client.post("/api/v1/llm-endpoints", headers=admin_headers, json=replacement).status_code
        == 409
    )
    with SessionLocal() as db:
        endpoint = db.get(LLMEndpoint, UUID(endpoint_id))
        assert endpoint.deleted_at and endpoint.updated_at >= endpoint.deleted_at


def test_agent_channel_key_is_reused_rotated_and_bound_to_its_url(
    client, admin_headers, body, provider
):
    channel_path = "/api/v1/agent-configs/inspection/channels"
    channel_body = {**body, "name": "Managed channel", "expected_version": 1}
    config = client.post(channel_path, headers=admin_headers, json=channel_body)
    assert config.status_code == 201, config.text
    channel_id = config.json()["channels"][0]["id"]
    channel_path += f"/{channel_id}"
    endpoints = "/api/v1/llm-endpoints"
    linked = {k: v for k, v in body.items() if k != "api_key"}
    linked["agent_channel_id"] = channel_id
    for invalid in (
        {**linked, "base_url": provider["base_url"] + "/other"},
        {**linked, "agent_channel_id": str(uuid4())},
        {**linked, "api_key": KEY},
    ):
        reply = client.post(endpoints, headers=admin_headers, json=invalid)
        assert reply.status_code == 422 and KEY not in reply.text
    created = client.post(endpoints, headers=admin_headers, json=linked)
    assert created.status_code == 201, created.text
    assert created.json()["agent_channel_id"] == channel_id and KEY not in created.text
    endpoint_id = created.json()["id"]
    with SessionLocal() as db:
        assert db.get(LLMEndpoint, UUID(endpoint_id)).encrypted_api_key is None
    endpoint_path = f"{endpoints}/{endpoint_id}"

    def probe():
        reply = client.post(endpoint_path + "/probe", headers=admin_headers)
        assert reply.status_code == 200, reply.text
        return reply.json()

    assert probe()["success"]
    assert provider["requests"][-1][1] == f"Bearer {KEY}"
    rotated = "rotated-agent-test-key"
    updated = client.put(
        channel_path,
        headers=admin_headers,
        json={**channel_body, "expected_version": 2, "api_key": rotated},
    )
    assert updated.status_code == 200, updated.text
    assert probe()["success"]
    assert provider["requests"][-1][1] == f"Bearer {rotated}"
    moved = client.put(
        channel_path,
        headers=admin_headers,
        json={**channel_body, "expected_version": 3, "base_url": body["base_url"] + "/other"},
    )
    assert moved.status_code == 200, moved.text
    assert probe()["error_type"] == "credential_error"
    assert len(provider["requests"]) == 2
    replacement = client.put(
        endpoint_path,
        headers=admin_headers,
        json={**body, "agent_channel_id": None},
    )
    assert replacement.status_code == 200 and replacement.json()["agent_channel_id"] is None
    assert probe()["success"]
    assert provider["requests"][-1][1] == f"Bearer {KEY}"


@pytest.mark.parametrize(
    "field,value",
    [
        ("api_key", ""),
        ("api_key", "key\nsecret"),
        ("base_url", "ftp://host"),
        ("base_url", "https://user:secret@host"),
        ("base_url", "https://host?key=secret"),
        ("base_url", "https://host#fragment"),
        ("base_url", "https://host:0"),
        ("probe_interval_seconds", 59),
        ("probe_interval_seconds", 86401),
        ("model", ""),
        ("provider", "unimplemented"),
    ],
)
def test_validation_redaction(client, admin_headers, body, field, value):
    response = client.post(
        "/api/v1/llm-endpoints", headers=admin_headers, json={**body, field: value}
    )
    assert response.status_code == 422
    assert KEY not in response.text and "key=secret" not in response.text


@pytest.mark.parametrize(
    "status,error",
    [
        (200, None),
        (401, "authentication_failed"),
        (402, "insufficient_balance"),
        (429, "rate_limited"),
        (500, "service_error"),
        (503, "service_error"),
        (400, "request_error"),
        (422, "request_error"),
        (302, "request_error"),
    ],
)
def test_actual_http_classification(client, admin_headers, provider, endpoint_id, status, error):
    provider["status"] = status
    response = client.post(f"/api/v1/llm-endpoints/{endpoint_id}/probe", headers=admin_headers)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["success"] == (error is None)
    assert result["error_type"] == error and result["http_status"] == status
    assert result["latency_ms"] > 0 and KEY not in response.text
    assert len(provider["requests"]) == 1
    path, authorization, payload = provider["requests"][0]
    assert path == "/chat/completions" and authorization == f"Bearer {KEY}"
    assert payload == {
        "model": "configured-model",
        "messages": [{"role": "user", "content": "Return only OK."}],
        "max_tokens": 5,
        "stream": False,
        "thinking": {"type": "disabled"},
    }
    if error is None:
        assert result["input_tokens"] == 7 and result["output_tokens"] == 1
    with SessionLocal() as db:
        endpoint = db.get(LLMEndpoint, UUID(endpoint_id))
        assert endpoint.last_error_type == error and endpoint.last_probe_success == (error is None)
    assert client.get("/api/v1/monitoring/llm-metrics").status_code == 401
    exported = client.get("/api/v1/monitoring/llm-metrics", headers=SERVICE_HEADERS).text
    assert KEY not in exported
    assert (
        f'llm_probe_requests_total{{endpoint_id="{endpoint_id}",'
        f'outcome="{error or "success"}"}} 1.0' in exported
    )


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"choices": []},
        {"choices": [{"message": {"content": ""}}]},
        {"choices": [{"message": {"content": "wrong"}}]},
        {"choices": [{"message": {"content": "OK"}}], "usage": {"prompt_tokens": -1}},
    ],
)
def test_invalid_response(client, admin_headers, provider, endpoint_id, payload):
    provider["payload"] = payload
    result = client.post(f"/api/v1/llm-endpoints/{endpoint_id}/probe", headers=admin_headers).json()
    assert result["error_type"] == "invalid_response" and not result["success"]


def test_timeout_and_conflicts_do_not_block_health(
    client, admin_headers, provider, endpoint_id, body, monkeypatch
):
    provider["blocked"] = True
    monkeypatch.setattr("app.services.llm.probes.PROBE_TIMEOUT", 0.4)
    url = f"/api/v1/llm-endpoints/{endpoint_id}"
    with ThreadPoolExecutor() as executor:
        running = executor.submit(client.post, url + "/probe", headers=admin_headers)
        assert provider["entered"].wait(2)
        assert client.post(url + "/probe", headers=admin_headers).status_code == 409
        assert client.put(url, headers=admin_headers, json=body).status_code == 409
        assert client.delete(url, headers=admin_headers).status_code == 409
        started = time.monotonic()
        assert client.get("/health/live").status_code == 200
        assert time.monotonic() - started < 0.2
        assert running.result(3).json()["error_type"] == "timeout"
    provider["release"].set()
    assert len(provider["requests"]) == 1
    assert client.put(url, headers=admin_headers, json=body).status_code == 200


def test_connection_and_credential_failure(client, admin_headers, endpoint_id, body):
    with socket.socket() as closed:
        closed.bind(("127.0.0.1", 0))
        port = closed.getsockname()[1]
    body["base_url"] = f"http://127.0.0.1:{port}"
    url = f"/api/v1/llm-endpoints/{endpoint_id}"
    assert client.put(url, headers=admin_headers, json=body).status_code == 200
    assert (
        client.post(url + "/probe", headers=admin_headers).json()["error_type"]
        == "connection_failed"
    )
    with SessionLocal() as db:
        db.get(LLMEndpoint, UUID(endpoint_id)).encrypted_api_key = "corrupt"
        db.commit()
    assert (
        client.post(url + "/probe", headers=admin_headers).json()["error_type"]
        == "credential_error"
    )


def test_total_deadline_covers_slow_body(client, admin_headers, provider, endpoint_id, monkeypatch):
    provider["drip"] = True
    monkeypatch.setattr("app.services.llm.probes.PROBE_TIMEOUT", 0.2)
    result = client.post(f"/api/v1/llm-endpoints/{endpoint_id}/probe", headers=admin_headers).json()
    assert result["error_type"] == "timeout"
    assert 150 <= result["latency_ms"] < 600
    assert len(provider["requests"]) == 1


def test_scheduler_disabled_restart_and_freshness(
    client, admin_headers, endpoint_id, body, provider
):
    service = client.app.state.llm_probes
    client.portal.call(service.schedule_once)
    assert provider["entered"].wait(2)
    client.portal.call(wait_tasks, service)
    assert len(provider["requests"]) == 1
    client.portal.call(service.schedule_once)
    client.portal.call(wait_tasks, service)
    assert len(provider["requests"]) == 1
    body["probe_enabled"] = False
    url = f"/api/v1/llm-endpoints/{endpoint_id}"
    assert client.put(url, headers=admin_headers, json=body).json()["status"] == "disabled"
    client.portal.call(service.schedule_once)
    assert len(provider["requests"]) == 1
    assert (
        endpoint_id
        not in client.get("/api/v1/monitoring/llm-metrics", headers=SERVICE_HEADERS).text
    )
    assert client.post(url + "/probe", headers=admin_headers).json()["success"]
    body.pop("api_key")
    body["probe_enabled"] = True
    assert client.put(url, headers=admin_headers, json=body).status_code == 200
    with SessionLocal() as db:
        db.get(LLMEndpoint, UUID(endpoint_id)).last_probe_at = datetime.now(UTC) - timedelta(
            minutes=3
        )
        db.commit()
    assert client.get(url, headers=admin_headers).json()["status"] == "stale"

    async def restarted():
        new_service = ProbeService()
        await new_service.schedule_once()
        await wait_tasks(new_service)
        await new_service.close()

    client.portal.call(restarted)
    assert len(provider["requests"]) == 3
    assert client.get(url, headers=admin_headers).json()["status"] == "healthy"


async def wait_tasks(service):
    await asyncio.gather(*list(service.tasks))


def test_global_concurrency_limit(client, admin_headers, body, monkeypatch):
    ids = []
    for index in range(6):
        body["name"] = f"endpoint-{index}"
        ids.append(
            UUID(
                client.post("/api/v1/llm-endpoints", headers=admin_headers, json=body).json()["id"]
            )
        )

    async def check():
        service = ProbeService()
        active, maximum = 0, 0
        saturated, release = asyncio.Event(), asyncio.Event()

        async def slow(endpoint):
            nonlocal active, maximum
            active += 1
            maximum = max(maximum, active)
            if active == 4:
                saturated.set()
            await release.wait()
            await asyncio.sleep(0)
            active -= 1
            return ProbeResult(
                endpoint_id=endpoint.id,
                checked_at=datetime.now(UTC),
                success=True,
                error_type=None,
                http_status=200,
                latency_ms=50,
            )

        monkeypatch.setattr("app.services.llm.probes.infer", slow)
        tasks = [asyncio.create_task(service.run(endpoint_id)) for endpoint_id in ids]
        try:
            await asyncio.wait_for(saturated.wait(), timeout=5)
        finally:
            release.set()
        await asyncio.gather(*tasks)
        assert maximum == 4
        await service.close()

    client.portal.call(check)


def test_metrics_queries_and_null_samples(client, admin_headers, endpoint_id, monkeypatch):
    captured = []

    def query(path, params):
        captured.append((path, params))
        return {
            "resultType": "vector",
            "result": [{"metric": {"endpoint_id": endpoint_id}, "value": [1, "NaN"]}],
        }

    monkeypatch.setattr("app.services.llm.management.prometheus", query)
    url = f"/api/v1/llm-endpoints/{endpoint_id}/metrics"
    response = client.get(url, headers=admin_headers)
    assert response.json()["result"][0]["value"][1] is None
    assert endpoint_id in captured[-1][1]["query"] and "[5m]" in captured[-1][1]["query"]
    start = datetime.now(UTC) - timedelta(hours=1)
    end = datetime.now(UTC)
    assert (
        client.get(url, headers=admin_headers, params={"start": start.isoformat()}).status_code
        == 422
    )
    assert (
        client.get(url, headers=admin_headers, params={"metric": "arbitrary_promql"}).status_code
        == 422
    )
    assert (
        client.get(
            url, headers=admin_headers, params={"start": start.isoformat(), "end": end.isoformat()}
        ).status_code
        == 200
    )
    assert captured[-1][0].endswith("query_range")
    assert (
        client.get(
            url,
            headers=admin_headers,
            params={"start": (end - timedelta(days=32)).isoformat(), "end": end.isoformat()},
        ).status_code
        == 422
    )


def test_status_empty_and_real_probe_results(client, admin_headers, endpoint_id, provider):
    url = f"/api/v1/llm-endpoints/{endpoint_id}"
    result = client.get(url + "/status", headers=admin_headers)
    assert result.status_code == 200
    summary = result.json()
    assert summary["source"] == "active_probe"
    assert summary["success_rate"] is None
    assert summary["request_count"] == summary["success_count"] == 0
    assert summary["last_request_at"] is None
    assert len(summary["buckets"]) == 60
    assert all(bucket["request_count"] == 0 for bucket in summary["buckets"])

    assert client.post(url + "/probe", headers=admin_headers).json()["success"]
    provider["status"] = 429
    assert not client.post(url + "/probe", headers=admin_headers).json()["success"]
    summary = client.get(url + "/status", headers=admin_headers).json()
    assert summary["request_count"] == 2
    assert summary["success_count"] == 1
    assert summary["success_rate"] == 0.5
    assert summary["status"] == "failed"
    assert summary["last_request_at"]
    assert sum(bucket["request_count"] for bucket in summary["buckets"]) == 2
    assert KEY not in str(summary)


def test_status_window_buckets_and_source_isolation(
    client, admin_headers, endpoint_id, monkeypatch
):
    from app.models import ModelUsage

    now = datetime.now(UTC)
    start = now - timedelta(minutes=30)
    epoch = start + timedelta(seconds=30)

    class Clock:
        @staticmethod
        def now(tz):
            return now

    monkeypatch.setattr("app.services.llm.management.datetime", Clock)
    with SessionLocal() as db:
        db.get(LLMEndpoint, UUID(endpoint_id)).probe_config_changed_at = epoch
        samples = [
            (start - timedelta(seconds=1), "success", {}),
            (start, "success", {}),  # Before the current endpoint configuration.
            (epoch, "success", {}),
            (epoch + timedelta(seconds=29), "failed", {}),
            (epoch + timedelta(seconds=30), "failed", {}),
            (now - timedelta(microseconds=1), "success", {}),
            (now, "success", {}),  # End of the window is exclusive.
            (epoch, "success", {"deleted_at": now}),
            (epoch, "success", {"source": "platform_gateway"}),
            (epoch, "success", {"resource_id": uuid4()}),
            (epoch, "unknown", {}),
        ]
        for index, (at, outcome, extra) in enumerate(samples):
            values = {
                "dedupe_key": f"status-test-{index}",
                "source": "llm_probe",
                "provider_kind": "model",
                "resource_kind": "llm_endpoint",
                "resource_id": UUID(endpoint_id),
                "purpose": "probe",
                "operation": "llm_probe",
                "model": "configured-model",
                "occurred_at": at,
                "calls": 1,
                "outcome": outcome,
                **extra,
            }
            db.add(ModelUsage(**values))
        db.commit()

    summary = client.get(
        f"/api/v1/llm-endpoints/{endpoint_id}/status", headers=admin_headers
    ).json()
    assert summary["request_count"] == 4
    assert summary["success_count"] == 2
    assert summary["success_rate"] == 0.5
    assert summary["buckets"][0]["request_count"] == 0
    assert summary["buckets"][1]["request_count"] == 2
    assert summary["buckets"][1]["success_count"] == 1
    assert summary["buckets"][2]["request_count"] == 1
    assert summary["buckets"][2]["success_count"] == 0
    assert summary["buckets"][59]["request_count"] == 1
    assert datetime.fromisoformat(summary["start"]) == start
    assert datetime.fromisoformat(summary["end"]) == now
    assert datetime.fromisoformat(summary["buckets"][59]["end"]) == now


def test_status_read_permissions_and_deleted_endpoint(
    client, admin_headers, endpoint_id, make_user, login
):
    url = f"/api/v1/llm-endpoints/{endpoint_id}"
    assert client.get(url + "/status").status_code == 401
    make_user("status-viewer", "viewer")
    headers = {"Authorization": f"Bearer {login('status-viewer')['access_token']}"}
    assert client.get(url + "/status", headers=headers).status_code == 200
    assert (
        client.put(
            f"/api/v1/access-policies/llm_endpoint/{endpoint_id}",
            headers=admin_headers,
            json={"restricted": True, "expected_version": 0},
        ).status_code
        == 200
    )
    assert client.get(url + "/status", headers=headers).status_code == 403
    assert client.get(url + "/status", headers=admin_headers).status_code == 200
    assert client.delete(url, headers=admin_headers).status_code == 204
    assert client.get(url + "/status", headers=admin_headers).status_code == 404
    for metric in (
        "llm_up",
        "llm_authentication_failed",
        "llm_insufficient_balance",
        "llm_rate_limited",
        "llm_service_error",
        "llm_probe_stale",
        "llm_latency_p95_seconds",
        "llm_latency_avg_seconds",
        "llm_error_rate",
        "llm_http_status",
    ):
        assert endpoint_id in llm_metric_expression(metric, endpoint_id)


def test_llm_rules_and_webhook_recovery(client, admin_headers, endpoint_id, body, monkeypatch):
    rule_body = {
        "code": "LLMAuthFailed",
        "llm_endpoint_id": endpoint_id,
        "metric": "llm_authentication_failed",
        "threshold": 0,
        "for_seconds": 0,
    }
    response = client.post("/api/v1/alert-rules", headers=admin_headers, json=rule_body)
    assert response.status_code == 201
    rule_id = response.json()["id"]
    assert (
        client.post(
            "/api/v1/alert-rules",
            headers=admin_headers,
            json={**rule_body, "server_id": str(uuid4())},
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/v1/alert-rules",
            headers=admin_headers,
            json={**rule_body, "metric": "host_cpu_percent"},
        ).status_code
        == 422
    )
    monkeypatch.setattr("app.services.monitoring.config.prometheus", lambda *a, **k: {})
    applied = []
    monkeypatch.setattr(
        "app.services.monitoring.config.verify_active_rules", lambda rules: applied.append(rules)
    )
    assert (
        client.post("/api/v1/monitoring/apply-rules", headers=admin_headers).json()["applied"] == 1
    )
    assert endpoint_id in applied[-1][0]["expr"]
    assert applied[-1][0]["labels"]["error_type"] == "authentication_failed"
    start = datetime.now(UTC) - timedelta(seconds=20)
    alert = {
        "fingerprint": "123abc",
        "status": "firing",
        "startsAt": start.isoformat(),
        "endsAt": "0001-01-01T00:00:00Z",
        "labels": {
            "endpoint_id": endpoint_id,
            "rule_id": rule_id,
            "error_type": "authentication_failed",
        },
    }
    payload = {"alerts": [alert]}
    hook = "/api/v1/hooks/alertmanager"
    assert client.post(hook, headers=SERVICE_HEADERS, json=payload).json()["changed"] == 1
    assert client.post(hook, headers=SERVICE_HEADERS, json=payload).json()["changed"] == 0
    alert["status"] = "resolved"
    alert["endsAt"] = datetime.now(UTC).isoformat()
    assert client.post(hook, headers=SERVICE_HEADERS, json=payload).json()["changed"] == 1
    events = client.get(
        "/api/v1/alerts", headers=admin_headers, params={"llm_endpoint_id": endpoint_id}
    ).json()
    assert (
        len(events) == 1
        and events[0]["llm_endpoint_id"] == endpoint_id
        and events[0]["status"] == "resolved"
    )
    body["probe_enabled"] = False
    assert (
        client.put(
            f"/api/v1/llm-endpoints/{endpoint_id}", headers=admin_headers, json=body
        ).status_code
        == 200
    )
    assert (
        client.post("/api/v1/monitoring/apply-rules", headers=admin_headers).json()["applied"] == 0
    )
    with SessionLocal() as db:
        endpoint = db.scalar(select(LLMEndpoint).where(LLMEndpoint.id == UUID(endpoint_id)))
        before = endpoint.updated_at
        db.execute(
            text("UPDATE llm_endpoints SET name='sql-updated' WHERE id=:id"), {"id": endpoint.id}
        )
        db.commit()
        db.refresh(endpoint)
        assert endpoint.updated_at > before
