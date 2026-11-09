"""Real query HTTP and container execution; model decisions are explicitly simulated."""

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.integrations import inspection_python
from app.models import InspectionItem, InspectionRun, KnowledgeSkill
from app.schemas.inspections import InspectionAPIRequest, InspectionSource
from app.services.inspections import agent, runtime, workflow


@pytest.fixture
def query_api():
    state = {"calls": [], "hook": None, "flagged": True}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            state["calls"].append(self.path)
            if state["hook"]:
                state["hook"]()
            if self.path == "/projects":
                data = [
                    {"id": "a", "special": state["flagged"]},
                    {"id": "b", "special": state["flagged"]},
                    {"id": "c", "special": False},
                ]
            elif self.path.startswith("/projects/"):
                identifier = self.path.split("/")[-1]
                data = {
                    "id": identifier,
                    "amount": 10 if identifier == "a" else -1,
                    "token": "fixture-secret",
                    "echo": self.headers.get("X-API-Key"),
                    "echo_raw": self.headers.get("Authorization", "").removeprefix("Bearer "),
                }
            else:
                data = {}
            raw = json.dumps(data).encode()
            self.send_response(200)
            self.end_headers()
            self.wfile.write(raw)

        def do_POST(self):
            state["calls"].append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"found":true}')

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    state["url"] = f"http://127.0.0.1:{server.server_port}"
    yield state
    server.shutdown()
    server.server_close()
    thread.join(timeout=3)


@pytest.fixture
def configured(client, admin_headers, query_api):
    def source(name, path, **config):
        reply = client.post(
            "/api/v1/inspection-data-sources",
            headers=admin_headers,
            json={
                "name": name,
                "environment": "test",
                "source_kind": "api",
                "source_config": {
                    "url": query_api["url"] + path,
                    "agent_api_enabled": True,
                    "response_format": "json",
                    **config,
                },
                "headers": {
                    "X-API-Key": "fixture-secret",
                    "Authorization": "Bearer fixture-bearer",
                },
            },
        )
        assert reply.status_code == 201, reply.text
        return reply.json()

    main = source("projects", "/projects")
    detail = source("detail", "", path_template="projects/{id}")
    skill = client.post(
        "/api/v1/skills",
        headers=admin_headers,
        json={
            "code": "project_review",
            "title": "项目检查",
            "content": "筛选 special=true；详情 amount 应大于0。",
            "scripts": {
                "scripts/select.py": (
                    "rows = next(iter(data['evidence'].values()))\n"
                    "result = [x['id'] for x in rows if x['special']]"
                )
            },
        },
    )
    assert skill.status_code == 201, skill.text
    body = {
        "name": "自主项目检查",
        "environment": "test",
        "enabled": True,
        "execution_mode": "agent",
        "agent_enabled": True,
        "skill_id": skill.json()["id"],
        "data_source_id": main["id"],
        "agent_source_ids": [detail["id"]],
        "agent_parameters": {"minimum": 0},
        "rules": [],
        "schedule_kind": "manual",
    }
    reply = client.post("/api/v1/inspection-plans", headers=admin_headers, json=body)
    assert reply.status_code == 201, reply.text
    return {
        "plan": reply.json(),
        "body": body,
        "main": main,
        "detail": detail,
        "skill": skill.json(),
    }


def start(client, headers, configured):
    reply = client.post(
        f"/api/v1/inspection-plans/{configured['plan']['id']}/runs", headers=headers
    )
    assert reply.status_code == 202, reply.text
    assert runtime.run_once()
    with SessionLocal() as db:
        item = db.scalar(
            select(InspectionItem).where(InspectionItem.run_id == UUID(reply.json()["id"]))
        )
        return workflow.WorkflowTools(item.run_id, item.id)


def test_paused_agent_plan_starts_on_demand(client, admin_headers, configured):
    plan = configured["plan"]
    response = client.put(
        f"/api/v1/inspection-plans/{plan['id']}/state",
        headers=admin_headers,
        json={"enabled": False, "expected_version": plan["version"]},
    )
    assert response.status_code == 200, response.text
    tools = start(client, admin_headers, configured)
    assert tools.call("read_inspection_skill", {})["parameters"] == {"minimum": 0}


@pytest.fixture
def sandbox(monkeypatch):
    settings = get_settings()
    if not settings.inspection_sandbox_url:
        pytest.skip("Set INSPECTION_SANDBOX_URL to the local test broker")
    return settings


def test_agent_selects_multiple_projects_and_persists_results(
    client, admin_headers, configured, query_api, monkeypatch, sandbox
):
    tools = start(client, admin_headers, configured)
    # Collection has not called the list API: the Agent determines the actual flow.
    assert query_api["calls"] == []
    assert tools.call("read_inspection_skill", {})["parameters"] == {"minimum": 0}
    assert len(tools.call("list_inspection_apis", {})) == 2
    listing = tools.call("request_inspection_api", {"source_id": configured["main"]["id"]})
    selection = tools.call(
        "execute_inspection_python",
        {
            "script": "scripts/select.py",
            "input_ids": [listing["id"]],
        },
    )
    assert selection["data"]["result"] == ["a", "b"], selection
    findings = []
    for identifier in selection["data"]["result"]:
        detail = tools.call(
            "request_inspection_api",
            {
                "source_id": configured["detail"]["id"],
                "path_params": {"id": identifier},
            },
        )
        assert detail["data"]["token"] == "[REDACTED]"
        assert detail["data"]["echo"] == "[REDACTED]"
        assert detail["data"]["echo_raw"] == "[REDACTED]"
        check = tools.call(
            "execute_inspection_python",
            {
                "code": "result = next(iter(data['evidence'].values()))['amount'] > 0",
                "input_ids": [detail["id"]],
            },
        )
        findings.append(
            {
                "record_id": identifier,
                "status": "pass" if check["data"]["result"] else "fail",
                "summary": "详情金额必须大于0",
                "evidence_ids": [check["id"], selection["id"]],
            }
        )
    answer = {
        "summary": "检查两个特殊项目",
        "complete": True,
        "findings": findings,
        "evidence_ids": [listing["id"]],
    }
    result = workflow.validate_result(answer, tools)
    monkeypatch.setattr(agent, "evaluate", lambda *_args: result)
    assert runtime.run_once()
    assert runtime.run_once()
    with SessionLocal() as db:
        run = db.get(InspectionRun, tools.run_id)
        assert run.status == "completed", run.error_code
        assert run.summary["health_counts"] == {"pass": 1, "fail": 1}
        assert run.collected == 2
        items = db.scalars(
            select(InspectionItem).where(
                InspectionItem.run_id == run.id, InspectionItem.deleted_at.is_(None)
            )
        ).all()
        assert {i.record_id for i in items} == {"a", "b"}
        assert all(i.evidence["tool_calls"] for i in items)
        assert "fixture-secret" not in json.dumps([i.evidence for i in items])


@pytest.mark.parametrize("value", ["../other", "%2fadmin", "..", "a?x=y", "https://evil", "a\\b"])
def test_dynamic_path_cannot_escape_authorized_template(client, admin_headers, configured, value):
    tools = start(client, admin_headers, configured)
    entry = tools.call(
        "request_inspection_api",
        {
            "source_id": configured["detail"]["id"],
            "path_params": {"id": value},
        },
    )
    assert entry["success"] is False
    assert entry["data"]["error_code"] == "inspection_api_path_parameters"


def test_api_scope_evidence_and_failures(client, admin_headers, configured):
    tools = start(client, admin_headers, configured)
    rejected = tools.call("request_inspection_api", {"source_id": str(uuid4())})
    assert rejected["success"] is False
    missing = tools.call("execute_inspection_python", {"code": "result=1", "input_ids": ["forged"]})
    assert missing["success"] is False
    observed = tools.call("request_inspection_api", {"source_id": configured["main"]["id"]})
    result = {
        "summary": "检查",
        "complete": True,
        "no_matches": True,
        "evidence_ids": [observed["id"]],
    }
    assert workflow.validate_result(result, tools)["complete"] is False
    with pytest.raises(ValueError, match="inspection_invalid_evidence"):
        workflow.validate_result({**result, "evidence_ids": [rejected["id"]]}, tools)
    tools.attempts = 64
    with pytest.raises(ValueError, match="inspection_tool_limit"):
        tools.call("list_inspection_apis", {})


def test_revocation_after_remote_io_discards_data(client, admin_headers, configured, query_api):
    tools = start(client, admin_headers, configured)

    def revoke():
        with SessionLocal() as db:
            skill = db.get(KnowledgeSkill, UUID(configured["skill"]["id"]))
            skill.enabled = False
            db.commit()

    query_api["hook"] = revoke
    with pytest.raises(ValueError, match="inspection_authorization_changed"):
        tools.call("request_inspection_api", {"source_id": configured["main"]["id"]})
    with SessionLocal() as db:
        assert db.get(InspectionItem, tools.item_id).evidence["tool_calls"] == []


def test_post_query_and_parameter_authorization(client, admin_headers, configured, query_api):
    from types import SimpleNamespace

    config = InspectionSource(
        url=query_api["url"] + "/search",
        agent_api_enabled=True,
        request_method="POST",
        allowed_body_fields=["ids"],
    )
    row = SimpleNamespace(source_config=config.model_dump(), encrypted_headers=None)
    args = InspectionAPIRequest(source_id=uuid4(), body={"ids": ["a", "b"]})
    assert workflow.request_api(row, args) == {"found": True}
    assert query_api["calls"] == [{"ids": ["a", "b"]}]
    with pytest.raises(ValueError, match="body_not_authorized"):
        workflow.request_api(row, args.model_copy(update={"body": {"delete": True}}))
    with pytest.raises(ValueError, match="query_not_authorized"):
        workflow.request_api(row, args.model_copy(update={"query": {"url": "http://evil"}}))


def test_no_matches_does_not_create_passing_business_projects(
    client, admin_headers, configured, query_api, monkeypatch
):
    query_api["flagged"] = False
    tools = start(client, admin_headers, configured)
    observed = tools.call("request_inspection_api", {"source_id": configured["main"]["id"]})
    result = workflow.validate_result(
        {
            "summary": "没有特殊项目",
            "complete": True,
            "no_matches": True,
            "evidence_ids": [observed["id"]],
        },
        tools,
    )
    monkeypatch.setattr(agent, "evaluate", lambda *_args: result)
    runtime.run_once()
    runtime.run_once()
    with SessionLocal() as db:
        run = db.get(InspectionRun, tools.run_id)
        assert run.summary["no_matches"] is True
        assert run.summary["health_counts"].get("pass", 0) == 0
        assert run.status == "completed"


def test_source_version_change_stops_current_workflow(client, admin_headers, configured):
    tools = start(client, admin_headers, configured)
    source = configured["detail"]
    reply = client.put(
        f"/api/v1/inspection-data-sources/{source['id']}",
        headers=admin_headers,
        json={
            "name": "Changed",
            "environment": "test",
            "source_kind": "api",
            "source_config": source["source_config"],
            "expected_version": source["version"],
        },
    )
    assert reply.status_code == 200, reply.text
    with pytest.raises(ValueError, match="inspection_authorization_changed"):
        tools.call("list_inspection_apis", {})


def test_agent_api_references_prevent_source_deletion(client, admin_headers, configured):
    path = f"/api/v1/inspection-data-sources/{configured['detail']['id']}"
    assert client.delete(path, headers=admin_headers).status_code == 409
    references = client.get(path + "/plans", headers=admin_headers)
    assert references.status_code == 200
    assert [row["id"] for row in references.json()] == [configured["plan"]["id"]]


def test_workflow_parameters_cannot_store_credentials(client, admin_headers, configured):
    reply = client.post(
        "/api/v1/inspection-plans",
        headers=admin_headers,
        json={
            **configured["body"],
            "agent_parameters": {"api_key": "credential-must-not-be-stored"},
        },
    )
    assert reply.status_code == 422


def test_sandbox_isolation_and_limits(sandbox):
    result = inspection_python.execute(
        """
import os, socket
result = {'uid': os.getuid(), 'files': os.path.exists('/app'),
          'secrets': os.path.exists('/run/secrets')}
try:
    socket.create_connection(('1.1.1.1', 80), timeout=.2)
    result['network'] = True
except OSError:
    result['network'] = False
try:
    open('/escape', 'w').write('x')
    result['writable_root'] = True
except OSError:
    result['writable_root'] = False
""",
        {},
    )
    assert result["result"] == {
        "uid": 65534,
        "files": False,
        "secrets": False,
        "network": False,
        "writable_root": False,
    }
    assert (
        inspection_python.execute("result = 'x' * 40000", {})["error_code"]
        == "python_execution_failed"
    )
    assert (
        inspection_python.execute("import time; time.sleep(20)", {})["error_code"]
        == "python_timeout"
    )
    assert inspection_python.execute("result = 2+3", {})["result"] == 5


def test_real_harness_mcp_workflow_with_local_provider(
    client, admin_headers, configured, sandbox, monkeypatch, tmp_path
):
    """Use the actual Harness process, MCP gateway and sandbox; no real LLM is called."""
    calls = []

    class Provider(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            assert {t["function"]["name"] for t in body["tools"]} == {
                "mcp__ops__" + name for name in workflow.WorkflowTools.definitions
            }
            messages = [m for m in body["messages"] if m["role"] == "tool"]
            calls.append(len(messages))
            steps = [
                ("read_inspection_skill", {}),
                ("list_inspection_apis", {}),
                ("request_inspection_api", {"source_id": configured["main"]["id"]}),
                (
                    "execute_inspection_python",
                    {
                        "script": "scripts/select.py",
                        "input_ids": ["e1"],
                    },
                ),
                (
                    "request_inspection_api",
                    {
                        "source_id": configured["detail"]["id"],
                        "path_params": {"id": "a"},
                    },
                ),
                (
                    "request_inspection_api",
                    {
                        "source_id": configured["detail"]["id"],
                        "path_params": {"id": "b"},
                    },
                ),
                (
                    "execute_inspection_python",
                    {
                        "code": (
                            "rows = data['evidence'].values()\n"
                            "result = {v['id']: v['amount'] > 0 for v in rows}"
                        ),
                        "input_ids": ["e3", "e4"],
                    },
                ),
            ]
            if len(messages) < len(steps):
                name, arguments = steps[len(messages)]
                delta = {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": "call_" + str(len(messages)),
                            "type": "function",
                            "function": {
                                "name": "mcp__ops__" + name,
                                "arguments": json.dumps(arguments),
                            },
                        }
                    ],
                }
                finish = "tool_calls"
            else:
                calculated = json.loads(messages[-1]["content"])
                assert calculated["success"], calculated
                assert calculated["data"]["result"] == {"a": True, "b": False}
                delta = {
                    "role": "assistant",
                    "content": json.dumps(
                        {
                            "summary": "检查完成",
                            "complete": True,
                            "evidence_ids": ["e1"],
                            "findings": [
                                {
                                    "record_id": name,
                                    "status": status,
                                    "summary": "详情金额必须大于0",
                                    "evidence_ids": ["e5"],
                                }
                                for name, status in [("a", "pass"), ("b", "fail")]
                            ],
                        }
                    ),
                }
                finish = "stop"
            event = {
                "id": "fixture",
                "object": "chat.completion.chunk",
                "created": 1,
                "model": body["model"],
                "choices": [{"index": 0, "delta": delta, "finish_reason": None}],
            }
            last = {
                **event,
                "choices": [{"index": 0, "delta": {}, "finish_reason": finish}],
                "usage": {"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30},
            }
            raw = (
                "data: "
                + json.dumps(event)
                + "\n\ndata: "
                + json.dumps(last)
                + "\n\ndata: [DONE]\n\n"
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Provider)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    key = tmp_path / "provider.key"
    key.write_text("local-provider-fixture-" + "x" * 32)
    settings = get_settings()
    monkeypatch.setattr(settings, "ai_enabled", True)
    monkeypatch.setattr(settings, "ai_provider_kind", "mock")
    monkeypatch.setattr(settings, "ai_key_file", key)
    monkeypatch.setattr(settings, "ai_base_url", f"http://127.0.0.1:{server.server_port}")
    from app.services.agent_config import import_environment

    with SessionLocal() as db:
        import_environment(db)
    errors = []
    original_evaluate = agent.evaluate

    def evaluate(*args):
        try:
            return original_evaluate(*args)
        except Exception as exc:
            import traceback

            errors.append(traceback.format_exc())
            raise exc

    monkeypatch.setattr(agent, "evaluate", evaluate)
    try:
        tools = start(client, admin_headers, configured)
        for _ in range(5):
            if not runtime.run_once():
                break
        with SessionLocal() as db:
            run = db.get(InspectionRun, tools.run_id)
            root = db.get(InspectionItem, tools.item_id)
            assert not errors, errors
            assert run.status == "completed", (run.error_code, root.agent_result)
            assert root.agent_status == "completed", root.agent_result
            assert run.summary["health_counts"] == {"pass": 1, "fail": 1}
        assert calls == list(range(8))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
