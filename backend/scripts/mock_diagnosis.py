"""Scripted local Provider; exercises the real Harness/MCP loop, not model quality."""

import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from uuid import uuid4

scenario = "normal"


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def reply(self, status, value):
        raw = json.dumps(value, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def report_reply(self, body):
        context = None
        for message in body["messages"]:
            if message["role"] == "tool":
                parsed = json.loads(message["content"])
                if parsed.get("source") == "get_postmortem_context":
                    context = parsed["data"]
        if context is None:
            delta = {
                "role": "assistant",
                "tool_calls": [
                    {
                        "index": 0,
                        "id": "call_" + uuid4().hex,
                        "type": "function",
                        "function": {"name": "mcp__ops__get_postmortem_context", "arguments": "{}"},
                    }
                ],
            }
            finish = "tool_calls"
        else:
            refs = [
                r["id"]
                for r in context["records"]
                if r["kind"] in ("incident", "alert", "analysis", "approval", "execution")
            ]
            result = {
                "summary": "本地模拟复盘：容器故障经人工审批恢复并完成事故关闭",
                "impact": "测试容器在告警区间停止，真实业务影响范围没有测量",
                "causes": [
                    {
                        "description": "测试人为停止容器；生产事故根因须依据日志确认",
                        "confidence": "suspected",
                        "evidence_ids": refs,
                    }
                ],
                "response_and_recovery": (
                    "平台记录了诊断、人工批准、固定重启动作和健康验证；监控独立确认恢复。"
                ),
                "lessons": ["区分 Docker 健康验证和业务影响测量"],
                "improvements": [
                    {
                        "action": "完善应用日志与业务可用性证据",
                        "owner": "待分配",
                        "deadline": "待确定",
                    }
                ],
                "data_gaps": context["data_gaps"],
                "evidence_ids": refs,
            }
            if scenario == "fake_evidence":
                result["evidence_ids"] = ["invented"]
            delta = {
                "role": "assistant",
                "content": "invalid-json"
                if scenario == "invalid_json"
                else json.dumps(result, ensure_ascii=False),
            }
            finish = "stop"
        item = {
            "id": "chatcmpl-local-report",
            "object": "chat.completion.chunk",
            "created": int(time.time()),
            "model": body["model"],
            "choices": [{"index": 0, "delta": delta, "finish_reason": None}],
        }
        last = {
            **item,
            "choices": [{"index": 0, "delta": {}, "finish_reason": finish}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 100, "total_tokens": 200},
        }
        raw = (
            "data: " + json.dumps(item) + "\n\ndata: " + json.dumps(last) + "\n\ndata: [DONE]\n\n"
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_POST(self):
        global scenario
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
        if self.path == "/scenario":
            scenario = str(body["scenario"])
            self.reply(200, {"scenario": scenario})
            return
        if self.path != "/chat/completions":
            self.reply(404, {})
            return
        if scenario == "context_limit":
            self.reply(
                400,
                {
                    "error": {
                        "code": "context_length_exceeded",
                        "message": "maximum context exceeded",
                    }
                },
            )
            return
        if scenario in ("401", "402", "429", "500", "503"):
            self.reply(int(scenario), {"error": {"message": "simulated provider failure"}})
            return
        if scenario == "timeout":
            time.sleep(190)
            return
        if any(
            t.get("function", {}).get("name") == "mcp__ops__get_postmortem_context"
            for t in body.get("tools", [])
        ):
            self.report_reply(body)
            return
        records, submission_errors = [], []
        chat = any(
            t.get("function", {}).get("name") == "mcp__ops__submit_diagnosis"
            for t in body.get("tools", [])
        )
        if chat and scenario == "chat_slow":
            time.sleep(0.5)
        for message in body["messages"]:
            if message["role"] == "tool":
                content = message.get("content", "")
                if content.startswith("Error: "):
                    content = content[len("Error: ") :]
                try:
                    parsed = json.loads(content)
                    if isinstance(parsed, dict) and "source" in parsed:
                        records.append(parsed)
                    elif isinstance(parsed, dict) and parsed.get("retryable"):
                        submission_errors.append(parsed)
                except (ValueError, TypeError):
                    pass
        prompt = next(m["content"] for m in body["messages"] if m["role"] == "user")
        initial = json.loads(prompt[prompt.index("{", prompt.index("\n")) :])
        context = initial["context"]["data"]
        containers = context["containers"]
        container = next(
            (c for c in containers if c["name"].endswith("test-worker-action-1")),
            next(
                (c for c in containers if "test-worker" in c["name"]),
                containers[0] if containers else None,
            ),
        )
        steps = [("get_alert_context", {})]
        if context["servers"]:
            steps.append(
                (
                    "get_server_metrics",
                    {"asset_id": context["servers"][0]["id"], "metric": "host_memory_percent"},
                )
            )
        if container:
            steps.extend(
                [
                    (
                        "get_container_metrics",
                        {"asset_id": container["id"], "metric": "container_memory_bytes"},
                    ),
                    ("get_container_status", {"asset_id": container["id"]}),
                    ("get_recent_logs", {"asset_id": container["id"]}),
                ]
            )
        steps.extend(
            [
                ("get_predictions", {}),
                ("list_skills", {}),
                ("search_knowledge", {"query": "Docker 容器停止 不可用 故障 检查 重启"}),
            ]
        )
        catalog = next((r["data"] for r in records if r["source"] == "list_skills"), [])
        default_skill = next((s for s in catalog if s["code"] == "sre_diagnosis"), None)
        if catalog:
            selected = default_skill or next(
                (s for s in catalog if s["code"] == "container_unavailable"), catalog[0]
            )
            position = next(i for i, (name, _) in enumerate(steps) if name == "list_skills")
            steps.insert(
                position + 1,
                ("read_skill", {"skill_id": selected["id"], "version": selected["version"]}),
            )
        proposal = (
            {
                "skill": "restart_container",
                "version": "1",
                "parameters": {"container_id": container["id"]},
                "risk": "medium",
                "rationale": "确认服务恢复条件后由人工决定是否重启",
                "impact": "重启会中断容器请求",
                "requires_human_approval": True,
                "executable": False,
            }
            if container
            else None
        )
        if (
            context["status"] == "recovered"
            or default_skill
            or not any(
                t.get("function", {}).get("name") == "mcp__ops__propose_action_skill"
                for t in body.get("tools", [])
            )
        ):
            proposal = None
        if proposal:
            steps.append(("propose_action_skill", proposal))
        if scenario == "database_diagnosis":
            connection = context["database_context"]["connections"][0]
            table = next(iter(connection["allowed_tables"]))
            target = {"connection_id": connection["connection_id"]}
            steps = [
                ("list_service_databases", {}),
                ("get_database_schema", {**target, "tables": [table]}),
                (
                    "query_database_readonly",
                    {
                        **target,
                        "sql": f"SELECT status,COUNT(*) AS total FROM {table} GROUP BY status",
                    },
                ),
            ]
            proposal = None
        if scenario == "source_diagnosis":
            steps = [("get_alert_context", {})]
            snapshots = context.get("source_context", {}).get("snapshots", [])
            current = next((s for s in snapshots if s["role"] in ("release", "reference")), None)
            previous = next((s for s in snapshots if s["role"] == "previous_release"), None)
            if current:
                steps.extend(
                    [
                        ("list_source_tree", {"snapshot_id": current["snapshot_id"]}),
                        (
                            "search_source",
                            {"snapshot_id": current["snapshot_id"], "query": "timeout"},
                        ),
                        (
                            "read_source_file",
                            {"snapshot_id": current["snapshot_id"], "path": "backend/app/main.py"},
                        ),
                    ]
                )
                if previous:
                    steps.append(
                        (
                            "compare_source_snapshots",
                            {
                                "snapshot_id": previous["snapshot_id"],
                                "other_id": current["snapshot_id"],
                                "path": "backend/app/main.py",
                            },
                        )
                    )
            proposal = None
        if scenario in (
            "chat_retry_evidence",
            "chat_retry_schema",
            "chat_retry_knowledge",
            "chat_abandon_submission",
        ):
            steps = [
                ("get_alert_context", {}),
                ("get_recent_logs", {"asset_id": container["id"]}),
            ]
            proposal = None
        if chat and scenario == "chat_reply":
            delta = {
                "role": "assistant",
                "content": "这是说明性回复；用户补充不能替代工具核验证据。\n",
            }
            finish = "stop"
        elif len(records) < len(steps):
            name, arguments = steps[len(records)]
            if scenario == "malicious":
                name, arguments = "bash", {"command": "touch /tmp/unauthorized"}
            elif scenario == "outside_asset":
                name, arguments = "get_container_status", {"asset_id": str(uuid4())}
            delta = {
                "role": "assistant",
                "tool_calls": [
                    {
                        "index": 0,
                        "id": "call_" + uuid4().hex,
                        "type": "function",
                        "function": {
                            "name": "mcp__ops__" + name if name != "bash" else name,
                            "arguments": json.dumps(arguments),
                        },
                    }
                ],
            }
            finish = "tool_calls"
        else:
            evidence_ids = [
                r["id"]
                for r in records
                if r["source"]
                in (
                    "get_alert_context",
                    "get_server_metrics",
                    "get_container_status",
                    "read_source_file",
                    "query_database_readonly",
                )
                and not (
                    isinstance(r.get("data"), dict)
                    and r["data"].get("availability") == "unavailable"
                )
            ]
            knowledge = initial["knowledge"]
            retrieved = [r for r in records if r["source"] == "search_knowledge"]
            if retrieved:
                knowledge += retrieved[-1]["data"]
            result = {
                "summary": "本地模拟诊断：事故已恢复，当前指标已复核"
                if context["status"] == "recovered"
                else "本地模拟诊断：容器故障与告警证据已收集",
                "possible_causes": [
                    {
                        "description": "容器进程停止；须结合应用日志确认原因",
                        "evidence_ids": evidence_ids,
                    }
                ],
                "impact": "容器承载服务存在不可用风险",
                "evidence_ids": evidence_ids,
                "knowledge_ids": list(dict.fromkeys(k["id"] for k in knowledge))[:5],
                "suggested_checks": ["核对容器状态及日志，确认恢复条件"],
                "data_gaps": ["测试资产未配置 SSH，容器日志不可用"],
                "proposal": proposal,
                "requires_human_approval": True,
            }
            if scenario == "database_diagnosis":
                result["summary"] = "本地模拟诊断：已核对数据库查询时的任务状态"
                result["possible_causes"][0]["description"] = "任务状态已获取，根因待进一步核实"
                result["data_gaps"] = []
            if scenario == "source_diagnosis":
                has_source = any(r["source"] == "read_source_file" for r in records)
                result["summary"] = (
                    "本地模拟诊断：源码证据已冻结"
                    if has_source
                    else "本地模拟诊断：使用运行上下文继续分析"
                )
                result["possible_causes"][0]["description"] = (
                    "已读取关联源码；版本差异只是排查线索，根因仍需运行证据确认"
                    if has_source
                    else "源码缺失，依据告警上下文继续排查"
                )
            if scenario == "fake_evidence":
                result["evidence_ids"] = ["invented-evidence"]
            if not submission_errors:
                if scenario in ("chat_retry_evidence", "chat_abandon_submission"):
                    failed = next(r for r in records if r["source"] == "get_recent_logs")
                    result["possible_causes"][0]["evidence_ids"].append(failed["id"])
                elif scenario == "chat_retry_schema":
                    result["summary"] = ""
                elif scenario == "chat_retry_knowledge":
                    result["knowledge_ids"] = ["invented-knowledge"]
            if (
                chat
                and not any(r["source"] == "submit_diagnosis" for r in records)
                and not (
                    submission_errors and scenario in ("fake_evidence", "chat_abandon_submission")
                )
            ):
                delta = {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": "call_" + uuid4().hex,
                            "type": "function",
                            "function": {
                                "name": "mcp__ops__submit_diagnosis",
                                "arguments": json.dumps(result, ensure_ascii=False),
                            },
                        }
                    ],
                }
                finish = "tool_calls"
            elif chat:
                supplements = [
                    m["content"]
                    for m in body["messages"]
                    if m["role"] == "user" and "用户补充" in str(m["content"])
                ]
                delta = {
                    "role": "assistant",
                    "content": "候选诊断未通过校验。\n"
                    if submission_errors
                    and scenario in ("fake_evidence", "chat_abandon_submission")
                    else (
                        "已读取事故上下文和可用的源码资料。\n"
                        if scenario == "source_diagnosis"
                        else "已读取告警、指标、容器状态并检索知识库。\n"
                    )
                    + ("已接收补充：" + str(supplements[-1]) + "\n" if supplements else "")
                    + "候选诊断已生成，请核对证据后手动采用。\n",
                }
                finish = "stop"
            else:
                delta = {
                    "role": "assistant",
                    "content": "invalid-json"
                    if scenario == "invalid_json"
                    else json.dumps(result, ensure_ascii=False),
                }
                finish = "stop"
        item = {
            "id": "chatcmpl-local",
            "object": "chat.completion.chunk",
            "created": int(time.time()),
            "model": body["model"],
            "choices": [{"index": 0, "delta": delta, "finish_reason": None}],
        }
        last = {
            **item,
            "choices": [{"index": 0, "delta": {}, "finish_reason": finish}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 100, "total_tokens": 200},
        }
        raw = (
            "data: "
            + json.dumps(item, ensure_ascii=False)
            + "\n\ndata: "
            + json.dumps(last)
            + "\n\ndata: [DONE]\n\n"
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        if not chat or scenario != "chat_slow":
            self.send_header("Content-Length", str(len(raw)))
        else:
            self.send_header("Connection", "close")
            self.close_connection = True
        self.end_headers()
        try:
            if chat and scenario == "chat_slow" and "content" in delta:
                for text in delta["content"].splitlines(keepends=True):
                    part = {
                        **item,
                        "choices": [
                            {"index": 0, "delta": {"content": text}, "finish_reason": None}
                        ],
                    }
                    self.wfile.write(("data: " + json.dumps(part) + "\n\n").encode())
                    self.wfile.flush()
                    time.sleep(0.3)
                self.wfile.write(("data: " + json.dumps(last) + "\n\ndata: [DONE]\n\n").encode())
            else:
                self.wfile.write(raw)
        except OSError:
            pass


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 8003), Handler).serve_forever()
