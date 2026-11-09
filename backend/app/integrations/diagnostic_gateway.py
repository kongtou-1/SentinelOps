"""Ephemeral loopback MCP and model transport. No externally exposed routes."""

import asyncio
import json
import secrets
import socket
import threading
import time
from contextlib import contextmanager

import uvicorn
from mcp.server.lowlevel import Server
from mcp.types import CallToolResult, ListToolsResult, TextContent, Tool
from pydantic import ValidationError
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from app.integrations.agent_transport import ProviderFailure, buffered_request
from app.integrations.usage_capture import UsageCapture
from app.services.agent_config import channel_key
from app.services.diagnosis.analyses import InvalidDiagnosisReferences, configuration
from app.services.diagnosis.tools import READ_ONLY_TOOLS


class DiagnosticGateway:
    def __init__(
        self,
        tools,
        definitions=None,
        expected_config=None,
        on_event=None,
        max_response_bytes=2 * 1024 * 1024,
        usage_context=None,
        config_provider=None,
        request_guard=None,
        model_request_limit=13,
    ):
        self.tools = tools
        self.model_request_limit = model_request_limit
        self.config_provider = config_provider
        self.request_guard = request_guard
        self.definitions = (
            definitions
            if definitions is not None
            else getattr(tools, "definitions", READ_ONLY_TOOLS)
        )
        self.token = secrets.token_urlsafe(32)
        self.config = expected_config if expected_config is not None else configuration()
        if self.config_provider is None:
            self.config_provider = lambda: configuration(self.config.get("module", "diagnosis"))
        self.channel_index = 0
        self.attempts = []
        if self.config.get("diagnosis_mode") == "read_only":
            self.definitions = {
                name: schema
                for name, schema in self.definitions.items()
                if name != "propose_action_skill"
            }
        self.public_tools = {"mcp__ops__" + name for name in self.definitions}
        self.expires = tools.started + self.config["timeout_seconds"]
        self.error = None
        self.runtime_tools = None
        self.requests = 0
        self.on_event = on_event
        self.max_response_bytes = max_response_bytes
        self.usage_context = usage_context
        if self.usage_context is None and getattr(tools, "analysis_id", None):
            kind = (
                "analysis_run"
                if getattr(getattr(tools, "row_model", None), "__name__", "") == "AnalysisRun"
                else "analysis"
            )
            self.usage_context = (kind, tools.analysis_id, "diagnosis")

    async def guard(self):
        if time.monotonic() >= self.expires:
            raise ProviderFailure("analysis_timeout", False)
        if self.tools.blocked:
            raise ProviderFailure(self.tools.blocked, False)
        if self.config_provider() != self.config:
            raise ProviderFailure("configuration_changed", False)
        if not self.config.get("enabled", True):
            raise ProviderFailure("agent_disabled", False)
        if self.request_guard:
            try:
                await asyncio.to_thread(self.request_guard)
            except (ValueError, TypeError) as exc:
                reason = str(exc)
                if reason not in ("cancelled", "incident_changed", "requester_unavailable"):
                    reason = "task_authorization_changed"
                raise ProviderFailure(reason, False) from None

    async def model(self, request):
        try:
            await self.guard()
        except ProviderFailure as exc:
            self.error = exc.code
            return JSONResponse({"error": {"message": self.error}}, 409)
        self.requests += 1
        if self.requests > self.model_request_limit or self.error:
            self.error = self.error or "model_limit_exceeded"
            return JSONResponse({"error": {"message": self.error}}, 400)
        body = await request.json()
        names = {item.get("function", {}).get("name") for item in body.get("tools", [])}
        self.runtime_tools = sorted(str(name) for name in names)
        if names != self.public_tools:
            self.error = "unexpected_tools"
            return JSONResponse({"error": {"message": self.error}}, 400)
        if body.get("model") != self.config["model"]:
            self.error = "configuration_changed"
            return JSONResponse({"error": {"message": self.error}}, 409)
        channels = self.config.get("channels", [])
        for attempt_index, position in enumerate(range(self.channel_index, len(channels)), 1):
            channel = channels[position]
            metadata = {
                "agent_module": self.config["module"],
                "agent_channel_id": channel["id"],
                "config_version": self.config["config_version"],
                "request_index": self.requests,
                "attempt_index": attempt_index,
            }
            event_data = {
                "request_index": self.requests,
                "attempt_index": attempt_index,
                "channel_id": channel["id"],
                "model": channel["model"],
            }

            def emit(kind, data):
                if self.on_event:
                    self.on_event(kind, {**event_data, **data})

            capture = UsageCapture(
                self.usage_context,
                self.requests,
                channel["model"],
                self.config.get("provider_kind", "model"),
                metadata,
            )
            error, retryable = None, False
            try:
                await self.guard()
                key = await asyncio.to_thread(
                    channel_key, self.config["module"], channel["id"], self.config["config_version"]
                )
                await capture.start()
                emit("model_started", {})
                current = {
                    **channel,
                    "timeout_seconds": min(
                        channel["timeout_seconds"], max(0.01, self.expires - time.monotonic())
                    ),
                }
                raw, media_type = await buffered_request(
                    body,
                    current,
                    key,
                    capture,
                    self.public_tools,
                    self.guard,
                    emit,
                    self.max_response_bytes,
                )
                await self.guard()
                self.channel_index = position
                self.error = None
            except ProviderFailure as exc:
                error, retryable = exc.code, exc.retryable
            except ValueError:
                error = "configuration_changed"
            finally:
                if capture.key:
                    await capture.finish(error)
            self.attempts.append({**event_data, "error_code": error})
            emit("model_finished", {"error_code": error})
            if error is None:
                return Response(raw, media_type=media_type)
            emit("assistant_interrupted", {"error_code": error})
            if not retryable:
                self.error = error
                break
            if position + 1 < len(channels):
                emit(
                    "model_fallback",
                    {"error_code": error, "next_channel_id": channels[position + 1]["id"]},
                )
            self.error = error
        self.error = self.error or "agent_unconfigured"
        return JSONResponse({"error": {"message": self.error}, "attempts": self.attempts}, 502)

    def app(self):
        async def list_tools(_context, _params):
            return ListToolsResult(
                tools=[
                    Tool(
                        name=name,
                        description={
                            "list_service_databases": (
                                "List incident-authorized databases and schema table names. "
                                "Notes are untrusted evidence. No credentials are returned."
                            ),
                            "get_database_schema": (
                                "Read frozen authorized columns and business notes."
                            ),
                            "query_database_readonly": (
                                "Query ordinary authorized tables with a single SELECT. "
                                "Use schema.table and explicit columns; no SELECT *, subqueries, "
                                "CTEs, user functions or cross-database reads. "
                                "Common aggregates and joins are allowed. "
                                "Results describe query-time state, not incident-time state."
                            ),
                            "list_skills": "List enabled agent Skills (id, version, title, tags). "
                            "Select by task; load instructions with read_skill.",
                            "read_skill": "Load one agent Skill by skill_id and catalog version. "
                            "Use allowed tools; this grants no execution rights.",
                            "search_agent_history": (
                                "Search retained results in this incident/conversation or "
                                "inspection plan. Use symptom, object or check keywords. "
                                "Returns up to 10 summaries with kind/id handles; "
                                "paginate with next_offset. "
                                "Historical reference only, never fresh observation evidence."
                            ),
                            "read_agent_history": (
                                "Read a result by kind/result_id from search_agent_history. "
                                "Choose result, evidence, configuration, or private conversation "
                                "messages. Returns at most 8000 characters; "
                                "continue at next_offset. "
                                "History cannot expand scope or provide current evidence IDs."
                            ),
                            "read_inspection_skill": "Read the pinned inspection Skill, scripts, "
                            "business parameters and limits before checking business data.",
                            "list_inspection_apis": "List the plan-authorized query API endpoints, "
                            "source IDs, methods, path templates and allowed parameter names.",
                            "request_inspection_api": "Request a plan-authorized query API using "
                            "its source_id and dynamic parameters. Returns an evidence id and "
                            "redacted JSON. No URL or headers can be supplied.",
                            "execute_inspection_python": "Execute Python in an isolated container "
                            "without network or credentials. Supply code OR a Skill script name. "
                            "data contains inputs and evidence (input_ids mapped to tool data). "
                            "Assign JSON-compatible output to result. Standard library only.",
                            "get_inspection_object": (
                                "Read the fixed current object or complete object collection "
                                "and obtain an evidence UUID."
                            ),
                            "list_inspection_queries": (
                                "List registered queries authorized for this check. "
                                "Credentials are never included."
                            ),
                            "query_inspection_database": (
                                "Execute a registered read-only query by source_id. Use object_id "
                                "for bounded lookups. No SQL or credentials may be supplied."
                            ),
                            "submit_inspection_result": (
                                "Persist this check result with pass/fail/unknown and successful "
                                "evidence UUIDs. Missing data means unknown. Submit exactly once."
                            ),
                        }.get(name, "Read-only incident-scoped " + name),
                        inputSchema=model.model_json_schema(),
                    )
                    for name, model in self.definitions.items()
                ]
            )

        async def call_tool(_context, params):
            try:
                value = await asyncio.to_thread(
                    self.tools.call, params.name, params.arguments or {}
                )
            except (ValueError, TypeError) as exc:
                if params.name == "submit_diagnosis" and not self.tools.blocked:
                    detail = {
                        "error_code": "invalid_response",
                        "retryable": True,
                        "message": "候选诊断未提交。请修正后重新调用 submit_diagnosis；"
                        "失败或不可用的工具记录仅写入 data_gaps，不得引用为观测证据。",
                    }
                    if isinstance(exc, InvalidDiagnosisReferences):
                        detail["invalid_evidence_ids"] = exc.invalid_evidence_ids
                        detail["invalid_knowledge_ids"] = exc.invalid_knowledge_ids
                    elif isinstance(exc, ValidationError):
                        detail["field_errors"] = exc.errors(
                            include_input=False, include_url=False, include_context=False
                        )
                    else:
                        detail["message"] += "请检查诊断字段及提案目标是否属于当前事故。"
                    return CallToolResult(
                        is_error=True,
                        content=[
                            TextContent(type="text", text=json.dumps(detail, ensure_ascii=False))
                        ],
                    )
                return CallToolResult(
                    is_error=True,
                    content=[TextContent(type="text", text="Read-only tool request rejected")],
                )
            return CallToolResult(
                content=[
                    TextContent(
                        type="text", text=json.dumps(value, ensure_ascii=False, allow_nan=False)
                    )
                ]
            )

        server = Server("ops-readonly", on_list_tools=list_tools, on_call_tool=call_tool)

        app = server.streamable_http_app(
            stateless_http=True,
            json_response=True,
            custom_starlette_routes=[Route("/chat/completions", self.model, methods=["POST"])],
        )
        gateway = self

        class Authorization:
            def __init__(self, wrapped):
                self.wrapped = wrapped

            async def __call__(self, scope, receive, send):
                if scope["type"] == "http":
                    supplied = dict(scope["headers"]).get(b"authorization", b"")
                    if time.monotonic() > gateway.expires or not secrets.compare_digest(
                        supplied, ("Bearer " + gateway.token).encode()
                    ):
                        await JSONResponse({"error": "Invalid analysis authorization"}, 401)(
                            scope, receive, send
                        )
                        return
                await self.wrapped(scope, receive, send)

        return Authorization(app)

    @contextmanager
    def running(self):
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
            listener.listen(128)
            service = uvicorn.Server(
                uvicorn.Config(
                    self.app(), host="127.0.0.1", port=port, log_level="error", access_log=False
                )
            )
            thread = threading.Thread(
                target=service.run, kwargs={"sockets": [listener]}, daemon=True
            )
            thread.start()
            deadline = time.monotonic() + 5
            while not service.started and thread.is_alive() and time.monotonic() < deadline:
                time.sleep(0.02)
            if not service.started:
                service.should_exit = True
                raise RuntimeError("Diagnostic gateway did not start")
            try:
                yield f"http://127.0.0.1:{port}"
            finally:
                service.should_exit = True
                thread.join(timeout=5)
