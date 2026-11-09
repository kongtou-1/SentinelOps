import json
import threading
import time
from contextlib import contextmanager
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.integrations.diagnostic_gateway import DiagnosticGateway
from app.models import AgentChannel, AgentConfig, ModelUsage, OperationsAuditLog
from app.schemas.diagnostic_tools import Empty
from app.services import agent_config
from app.services.operations import usage


@pytest.fixture
def enabled(monkeypatch):
    monkeypatch.setattr(get_settings(), "ai_enabled", True)
    monkeypatch.setattr(get_settings(), "ai_timeout_seconds", 30)


def create_channel(client, headers, url, module="diagnosis", model="model-a", name="channel"):
    config = next(
        c
        for c in client.get("/api/v1/agent-configs", headers=headers).json()
        if c["module"] == module
    )
    result = client.post(
        f"/api/v1/agent-configs/{module}/channels",
        headers=headers,
        json={
            "name": name,
            "base_url": url,
            "model": model,
            "api_key": "test-key-private",
            "expected_version": config["version"],
        },
    )
    assert result.status_code == 201, result.text
    return result.json()


@contextmanager
def provider(scenarios):
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            route = self.path.split("/")[1]
            requests.append((route, body))
            scenario = scenarios[route]
            if callable(scenario):
                scenario = scenario()
            status = scenario if isinstance(scenario, int) else 200
            self.send_response(status)
            stream = body.get("stream") and status == 200
            self.send_header("Content-Type", "text/event-stream" if stream else "application/json")
            self.end_headers()
            if status != 200:
                self.wfile.write(b'{"error":{"message":"private provider error"}}')
            elif not stream:
                self.wfile.write(
                    json.dumps(
                        {
                            "model": body["model"],
                            "choices": [
                                {
                                    "message": {"role": "assistant", "content": "OK"},
                                    "finish_reason": "stop",
                                }
                            ],
                            "usage": {"prompt_tokens": 11, "completion_tokens": 3},
                        }
                    ).encode()
                )
            else:
                delta = {"content": "draft" if scenario == "truncated" else "complete"}
                if scenario == "tool":
                    delta = {"tool_calls": [{"index": 0, "function": {"name": "forbidden"}}]}
                values = [
                    {"model": body["model"], "choices": [{"index": 0, "delta": delta}]},
                    {
                        "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                        "usage": {"prompt_tokens": 11, "completion_tokens": 3},
                    },
                ]
                if scenario == "truncated":
                    time.sleep(0.22)
                for value in values:
                    self.wfile.write(("data: " + json.dumps(value) + "\n\n").encode())
                    self.wfile.flush()
                if scenario != "truncated":
                    self.wfile.write(b"data: [DONE]\n\n")

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)


def gateway_request(gateway, stream=True):
    with TestClient(gateway.app()) as local:
        return local.post(
            "/chat/completions",
            headers={"Authorization": "Bearer " + gateway.token},
            json={
                "model": gateway.config["model"],
                "stream": stream,
                "messages": [{"role": "user", "content": "check"}],
                "tools": [
                    {"type": "function", "function": {"name": n}} for n in gateway.public_tools
                ],
            },
        )


def make_gateway(events=None, guard=None):
    return DiagnosticGateway(
        SimpleNamespace(started=time.monotonic(), blocked=None),
        definitions={"read": Empty},
        usage_context=("agent_channel", uuid4(), "diagnosis"),
        on_event=(lambda k, d: events.append((k, d))) if events is not None else None,
        request_guard=guard,
    )


def test_management_encrypts_keys_versions_and_isolates_modules(client, admin_headers, enabled):
    result = create_channel(client, admin_headers, "http://localhost/v1")
    assert result["version"] == 2
    channel = result["channels"][0]
    assert "api_key" not in channel and "encrypted_api_key" not in channel
    assert channel["key_configured"]
    with SessionLocal() as db:
        saved = db.get(AgentChannel, UUID(channel["id"]))
        assert saved.encrypted_api_key != "test-key-private"
        assert "test-key-private" not in json.dumps(
            [r.detail for r in db.scalars(select(OperationsAuditLog))]
        )
    original = agent_config.profile("inspection")
    response = client.put(
        f"/api/v1/agent-configs/diagnosis/channels/{channel['id']}",
        headers=admin_headers,
        json={
            "name": "edited",
            "base_url": "http://localhost/v1",
            "model": "model-b",
            "api_key": None,
            "expected_version": 2,
        },
    )
    assert response.status_code == 200
    assert agent_config.channel_key("diagnosis", channel["id"], 3) == "test-key-private"
    assert agent_config.profile("inspection") == original
    assert (
        client.request(
            "DELETE",
            f"/api/v1/agent-configs/diagnosis/channels/{channel['id']}",
            headers=admin_headers,
            json={"expected_version": 2},
        ).status_code
        == 409
    )
    assert (
        client.put(
            "/api/v1/agent-configs/diagnosis/channels/order",
            headers=admin_headers,
            json={"expected_version": 3, "channel_ids": []},
        ).status_code
        == 422
    )


def test_only_admin_can_mutate_even_when_operator_has_management_permission(
    client,
    make_user,
    login,
    enabled,
):
    make_user("operator", "operator")
    headers = {"Authorization": "Bearer " + login("operator")["access_token"]}
    assert client.get("/api/v1/agent-configs", headers=headers).status_code == 200
    assert (
        client.put(
            "/api/v1/agent-configs/diagnosis",
            headers=headers,
            json={"enabled": False, "expected_version": 1},
        ).status_code
        == 403
    )
    assert client.get("/api/v1/agent-configs").status_code == 401


def test_readiness_requires_current_agent_channel_migration(client, monkeypatch):
    assert client.get("/health/ready").status_code == 200
    monkeypatch.setattr(
        "app.repositories.system.ready_query", lambda db: "0036_inspection_agent_skills"
    )
    assert client.get("/health/ready").status_code == 503


def test_reorder_disable_and_soft_delete_preserve_usage(client, admin_headers, enabled):
    first = create_channel(client, admin_headers, "http://localhost/first")["channels"][0]
    config = create_channel(client, admin_headers, "http://localhost/second")
    second = config["channels"][1]
    config = client.put(
        "/api/v1/agent-configs/diagnosis/channels/order",
        headers=admin_headers,
        json={"expected_version": config["version"], "channel_ids": [second["id"], first["id"]]},
    ).json()
    assert agent_config.profile()["channels"][0]["id"] == second["id"]
    result = client.put(
        f"/api/v1/agent-configs/diagnosis/channels/{second['id']}",
        headers=admin_headers,
        json={
            "name": second["name"],
            "base_url": second["base_url"],
            "model": second["model"],
            "enabled": False,
            "expected_version": config["version"],
        },
    )
    assert result.status_code == 200
    config = result.json()
    assert [c["id"] for c in agent_config.profile()["channels"]] == [first["id"]]
    key = usage.start_gateway(
        ("agent_channel", uuid4(), "diagnosis"),
        1,
        first["model"],
        "model",
        {
            "agent_module": "diagnosis",
            "agent_channel_id": first["id"],
            "config_version": config["version"],
            "request_index": 1,
            "attempt_index": 1,
        },
    )
    usage.finish_gateway(key, {"prompt_tokens": 2, "completion_tokens": 1}, 0.1, None, "success")
    deleted = client.request(
        "DELETE",
        f"/api/v1/agent-configs/diagnosis/channels/{first['id']}",
        headers=admin_headers,
        json={"expected_version": config["version"]},
    )
    assert deleted.status_code == 200
    assert not agent_config.profile()["channels"]
    with SessionLocal() as db:
        channel = db.get(AgentChannel, UUID(first["id"]))
        assert channel.deleted_at and channel.updated_at >= channel.deleted_at
        record = db.scalar(select(ModelUsage))
        assert str(record.agent_channel_id) == first["id"]
        assert record.detail["channel_name"] == first["name"]


def test_failover_buffers_partial_tools_and_records_every_attempt(client, admin_headers, enabled):
    with provider({"first": 401, "second": "truncated", "third": "ok"}) as (url, requests):
        for route in ("first", "second", "third"):
            create_channel(client, admin_headers, f"{url}/{route}", model=route)
        events = []
        gateway = make_gateway(events)
        result = gateway_request(gateway)
        assert result.status_code == 200, result.text
        assert "draft" not in result.text and "complete" in result.text
        assert '"model": "first"' in result.text  # Stable Harness-facing model alias.
        assert gateway.error is None
        assert [r[0] for r in requests] == ["first", "second", "third"]
        assert [r[1]["model"] for r in requests] == ["first", "second", "third"]
        assert all(r[1]["messages"] == requests[0][1]["messages"] for r in requests)
        assert [e[1]["attempt_index"] for e in events if e[0] == "model_fallback"] == [1, 2]
        assert gateway_request(gateway).status_code == 200
        assert requests[-1][0] == "third" and len(requests) == 4
        with SessionLocal() as db:
            rows = db.scalars(select(ModelUsage).order_by(ModelUsage.occurred_at)).all()
            assert [r.outcome for r in rows] == ["failed", "failed", "success", "success"]
            assert len({r.dedupe_key for r in rows}) == 4
            assert rows[0].input_tokens is None
            assert rows[1].input_tokens == 11  # A truncated reply may still report real usage.
            assert rows[2].model == "third" and rows[2].attempt_index == 3
        # A new task starts from the primary again.
        assert gateway_request(make_gateway()).status_code == 200
        assert requests[4][0] == "first"


@pytest.mark.parametrize(
    "scenario,expected,count", [(503, "service_error", 2), ("tool", "tool_not_allowed", 1)]
)
def test_exhaustion_and_terminal_policy_failure(
    client, admin_headers, enabled, scenario, expected, count
):
    with provider({"first": scenario, "second": 503}) as (url, requests):
        create_channel(client, admin_headers, url + "/first")
        create_channel(client, admin_headers, url + "/second")
        gateway = make_gateway()
        response = gateway_request(gateway)
        assert response.status_code == 502 and gateway.error == expected
        assert len(requests) == count
        assert len(response.json()["attempts"]) == count


def test_guard_change_stops_before_backup(client, admin_headers, enabled):
    def stop():
        with SessionLocal() as db:
            config = db.get(AgentConfig, "diagnosis")
            config.version += 1
            db.commit()
        return 503

    with provider({"first": stop, "second": "ok"}) as (url, requests):
        create_channel(client, admin_headers, url + "/first")
        create_channel(client, admin_headers, url + "/second")
        gateway = make_gateway()
        assert gateway_request(gateway).status_code == 502
        assert gateway.error == "configuration_changed" and len(requests) == 1
        with SessionLocal() as db:
            assert len(db.scalars(select(ModelUsage)).all()) == 1


def test_cancellation_never_attempts_provider(client, admin_headers, enabled):
    with provider({"first": "ok"}) as (url, requests):
        create_channel(client, admin_headers, url + "/first")

        def cancelled():
            raise ValueError("cancelled")

        gateway = make_gateway(guard=cancelled)
        assert gateway_request(gateway).status_code == 409
        assert not requests


def test_attempt_timeout_uses_backup_with_remaining_task_budget(client, admin_headers, enabled):
    def slow():
        time.sleep(1.2)
        return "ok"

    with provider({"first": slow, "second": "ok"}) as (url, requests):
        create_channel(client, admin_headers, url + "/first")
        create_channel(client, admin_headers, url + "/second")
        with SessionLocal() as db:
            first = db.scalar(select(AgentChannel).order_by(AgentChannel.position))
            first.timeout_seconds = 1
            db.commit()
        gateway = make_gateway()
        assert gateway_request(gateway).status_code == 200
        assert [r[0] for r in requests] == ["first", "second"]
        assert gateway.attempts[0]["error_code"] == "provider_timeout"
        assert gateway.error is None


def test_partial_usage_and_module_channel_filters(client, admin_headers, enabled):
    config = create_channel(client, admin_headers, "http://localhost/v1", module="inspection")
    channel = config["channels"][0]
    key = usage.start_gateway(
        ("agent_channel", uuid4(), "inspection"),
        1,
        channel["model"],
        "model",
        {
            "agent_module": "inspection",
            "agent_channel_id": channel["id"],
            "config_version": config["version"],
            "request_index": 1,
            "attempt_index": 2,
        },
    )
    usage.finish_gateway(key, {"prompt_tokens": 7}, 0.25, None, "failed", "stream_incomplete")
    filters = f"?agent_module=inspection&agent_channel_id={channel['id']}&model={channel['model']}"
    records = client.get("/api/v1/system/model-usage" + filters, headers=admin_headers).json()
    assert len(records) == 1
    assert records[0]["input_tokens"] == 7 and records[0]["output_tokens"] is None
    assert records[0]["unknown_calls"] == 1
    assert "known_cost" not in records[0]
    groups = client.get(
        "/api/v1/system/model-usage/summary" + filters, headers=admin_headers
    ).json()["groups"]
    assert groups[0]["failed_calls"] == 1 and groups[0]["fallback_attempts"] == 1
    assert groups[0]["known_input_tokens"] == 7 and groups[0]["unknown_usage_records"] == 1
    assert (
        client.get(
            "/api/v1/system/model-usage?agent_module=diagnosis", headers=admin_headers
        ).json()
        == []
    )


def test_connection_test_is_metered_separately(client, admin_headers, enabled):
    with provider({"first": "ok"}) as (url, _):
        config = create_channel(client, admin_headers, url + "/first")
        result = client.post(
            f"/api/v1/agent-configs/diagnosis/channels/{config['channels'][0]['id']}/test",
            headers=admin_headers,
            json={"expected_version": config["version"]},
        )
        assert result.status_code == 200 and result.json()["success"]
        with SessionLocal() as db:
            row = db.scalar(select(ModelUsage))
            assert row.purpose == "connection_test" and row.input_tokens == 11
            channel = db.get(AgentChannel, row.agent_channel_id)
            assert channel.last_success and channel.last_latency_ms >= 0


@pytest.mark.parametrize(
    ("url", "thinking"),
    [
        ("https://api.deepseek.com", {"type": "disabled"}),
        ("https://api.deepseek.com/v1", {"type": "disabled"}),
        ("https://compatible.example/v1", None),
    ],
)
def test_connection_probe_handles_provider_default_thinking(
    client, admin_headers, enabled, monkeypatch, url, thinking
):
    from app.integrations.agent_transport import ProviderFailure

    async def provider_reply(body, *_args):
        # A default-thinking provider consumes a tiny probe budget before answering.
        if url.startswith("https://api.deepseek.com") and body.get("thinking") != thinking:
            raise ProviderFailure("model_output_limit", False)
        assert body.get("thinking") == thinking
        assert body["max_tokens"] >= 32

    monkeypatch.setattr("app.integrations.agent_transport.buffered_request", provider_reply)
    config = create_channel(client, admin_headers, url)
    result = client.post(
        f"/api/v1/agent-configs/diagnosis/channels/{config['channels'][0]['id']}/test",
        headers=admin_headers,
        json={"expected_version": config["version"]},
    )
    assert result.status_code == 200 and result.json()["success"]


def test_environment_import_is_idempotent_and_preserves_empty_configuration(
    enabled, tmp_path, monkeypatch
):
    key = tmp_path / "legacy.key"
    key.write_text("legacy-key-" + "x" * 32)
    monkeypatch.setattr(get_settings(), "ai_key_file", key)
    with SessionLocal() as db:
        assert len(agent_config.import_environment(db)) == 4
        assert agent_config.import_environment(db) == []
        diagnosis = db.get(AgentConfig, "diagnosis")
        diagnosis.version += 1
        for channel in db.scalars(select(AgentChannel).where(AgentChannel.module == "diagnosis")):
            channel.deleted_at = datetime.now(UTC)
        db.commit()
        assert agent_config.import_environment(db) == []
