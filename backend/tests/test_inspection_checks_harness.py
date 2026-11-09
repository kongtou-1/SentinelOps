"""Real Harness/MCP/HTTP/Python chain with a local, explicitly simulated model provider."""

import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from uuid import UUID

from sqlalchemy import select
from test_inspection_checks import create, execute, rule_check
from test_inspections import source as source

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.models import ModelUsage
from app.services.agent_config import import_environment
from app.services.inspections.check_agent import CheckTools


def test_real_harness_queries_data_executes_code_and_submits_results(
    client, admin_headers, source, monkeypatch, tmp_path
):
    source["data"] = {"id": "a", "amount": 20}
    query = client.post(
        "/api/v1/inspection-data-sources",
        headers=admin_headers,
        json={
            "name": "金额详情",
            "environment": "test",
            "source_kind": "api",
            "source_config": {
                "url": source["url"],
                "agent_api_enabled": True,
                "response_format": "json",
            },
        },
    )
    assert query.status_code == 201, query.text
    skill = client.post(
        "/api/v1/skills",
        headers=admin_headers,
        json={
            "code": "amount-inspection",
            "title": "金额核验",
            "content": "读取详情，计算金额差额，差额非零时不通过。",
            "resources": {"references/basis.md": "金额必须一致。"},
        },
    )
    assert skill.status_code == 201, skill.text
    calls = []

    class Provider(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            assert {t["function"]["name"] for t in body["tools"]} == {
                "mcp__ops__" + name for name in CheckTools.definitions
            }
            messages = [m for m in body["messages"] if m["role"] == "tool"]
            count = len(messages)
            calls.append(count)
            previous = [json.loads(m["content"]) for m in messages]
            steps = [
                ("read_inspection_skill", {}),
                ("get_inspection_object", {}),
                ("request_inspection_api", {"source_id": query.json()["id"]}),
                ("read_inspection_skill", {"path": "references/basis.md"}),
            ]
            if count >= 4:
                steps.append(
                    (
                        "execute_inspection_python",
                        {
                            "code": (
                                "values = list(data['evidence'].values())\n"
                                "result = {'difference': abs(values[0]['facts']['amount'] "
                                "- values[1]['amount'])}"
                            ),
                            "input_ids": [previous[1]["id"], previous[2]["id"]],
                        },
                    )
                )
            final = None
            if count >= 5:
                assert previous[4]["success"] and previous[4]["data"]["result"]["difference"] == 10
                final = {
                    "status": "fail",
                    "summary": "金额差额为10",
                    "evidence_ids": [previous[1]["id"], previous[2]["id"], previous[4]["id"]],
                    "metrics": {"difference": 10},
                }
                steps.append(("submit_inspection_result", final))
            if count < len(steps):
                name, args = steps[count]
                delta = {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": "call_" + str(count),
                            "type": "function",
                            "function": {
                                "name": "mcp__ops__" + name,
                                "arguments": json.dumps(args),
                            },
                        }
                    ],
                }
                finish = "tool_calls"
            else:
                delta, finish = {"role": "assistant", "content": json.dumps(final)}, "stop"
            chunk = {
                "id": "inspection-check-test",
                "object": "chat.completion.chunk",
                "created": int(time.time()),
                "model": body["model"],
                "choices": [{"index": 0, "delta": delta, "finish_reason": None}],
            }
            last = {
                **chunk,
                "choices": [{"index": 0, "delta": {}, "finish_reason": finish}],
                "usage": {"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30},
            }
            raw = (
                "data: "
                + json.dumps(chunk)
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
    settings = get_settings()
    key = tmp_path / "provider.key"
    key.write_text("local-simulated-inspection-provider", encoding="utf-8")
    monkeypatch.setattr(settings, "ai_enabled", True)
    monkeypatch.setattr(settings, "ai_key_file", key)
    monkeypatch.setattr(settings, "ai_base_url", f"http://127.0.0.1:{server.server_port}")
    monkeypatch.setattr(settings, "ai_timeout_seconds", 60)
    monkeypatch.setattr(settings, "ai_provider_kind", "mock")
    with SessionLocal() as db:
        import_environment(db)
    try:
        plan = create(
            client,
            admin_headers,
            check_items=[
                rule_check(),
                {
                    "check_id": "materials",
                    "name": "材料核验",
                    "kind": "agent",
                    "skill_id": skill.json()["id"],
                    "source_ids": [query.json()["id"]],
                    "requirements": "比较输入和详情金额。",
                },
            ],
        )
        run, rows = execute(client, admin_headers, plan)
        assert run["status"] == "completed", rows
        assert run["summary"]["coverage"]["conclusion"] == "fail"
        assert calls == list(range(7))
        traces = client.get(
            f"/api/v1/inspection-runs/{run['id']}/traces", headers=admin_headers
        ).json()
        assert traces and all(
            t["object_key"] == "a" and t["check_name"] == "材料核验" for t in traces
        )
        result_id = UUID(next(row["id"] for row in rows if row["kind"] == "agent"))
        with SessionLocal() as db:
            assert (
                len(db.scalars(select(ModelUsage).where(ModelUsage.resource_id == result_id)).all())
                == 7
            )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
