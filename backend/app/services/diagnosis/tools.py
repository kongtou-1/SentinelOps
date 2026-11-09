import json
import math
import time
from datetime import UTC, datetime, timedelta
from threading import RLock
from uuid import uuid4

from fastapi import HTTPException

import app.repositories.diagnosis.tools as _repository
from app.db.session import SessionLocal
from app.integrations.prometheus import prometheus
from app.integrations.ssh import read_container
from app.models import (
    AgentAnalysis,
)
from app.schemas.agent_results import HistoryRead, HistorySearch
from app.schemas.diagnosis import Proposal
from app.schemas.diagnostic_tools import Asset as Asset
from app.schemas.diagnostic_tools import Empty as Empty
from app.schemas.diagnostic_tools import LLMMetricInput as LLMMetricInput
from app.schemas.diagnostic_tools import MetricInput as MetricInput
from app.schemas.diagnostic_tools import SearchInput as SearchInput
from app.schemas.diagnostic_tools import ServiceMetricInput as ServiceMetricInput
from app.schemas.diagnostic_tools import SkillInput as SkillInput
from app.services.database_diagnosis import DATABASE_TOOLS, dispatch_database
from app.services.diagnosis.analyses import requester_valid, scope, scope_ids, validate_proposal
from app.services.incidents.logic import linked_alerts
from app.services.knowledge.index import search
from app.services.monitoring.metrics import llm_metric_expression
from app.services.resources.metrics import metrics_result
from app.services.sources.diagnostics import SOURCE_TOOLS, dispatch_source
from app.utils.redaction import redact

TOOLS = {
    **DATABASE_TOOLS,
    **SOURCE_TOOLS,
    "get_alert_context": Empty,
    "get_server_metrics": MetricInput,
    "get_container_metrics": MetricInput,
    "get_llm_metrics": LLMMetricInput,
    "get_service_metrics": ServiceMetricInput,
    "get_container_status": Asset,
    "get_recent_logs": Asset,
    "get_predictions": Empty,
    "search_knowledge": SearchInput,
    "search_agent_history": HistorySearch,
    "read_agent_history": HistoryRead,
    "list_skills": Empty,
    "read_skill": SkillInput,
    "propose_action_skill": Proposal,
}
READ_ONLY_TOOLS = {name: schema for name, schema in TOOLS.items() if name != "propose_action_skill"}
PUBLIC_TOOLS = {"mcp__ops__" + name for name in READ_ONLY_TOOLS}


class DiagnosticTools:
    def __init__(self, analysis_id, row_model=AgentAnalysis, definitions=None, on_event=None):
        self.analysis_id = analysis_id
        self.row_model = row_model
        self.definitions = definitions if definitions is not None else READ_ONLY_TOOLS
        self.on_event = on_event
        self.evidence, self.knowledge, self.calls = [], [], []
        self.started = time.monotonic()
        self.blocked = None
        self.attempts = 0
        self.lock = RLock()

    def call(self, name, arguments, internal=False):
        safe_arguments = redact(arguments)
        if name == "query_database_readonly":
            safe_arguments = {
                "connection_id": arguments.get("connection_id"),
                "sql": "[read-only query; literals omitted]",
            }
        with self.lock:
            if not internal:
                if self.attempts >= 12:
                    self.blocked = "tool_limit_exceeded"
                    raise ValueError("Tool call limit exceeded")
                self.attempts += 1
            call_id, started = uuid4().hex, time.monotonic()
            if self.on_event:
                self.on_event(
                    "tool_started",
                    {
                        "call_id": call_id,
                        "tool": name,
                        "arguments": safe_arguments,
                        "internal": internal,
                    },
                )
            try:
                result = self._call(name, arguments, internal)
            except Exception:
                if self.on_event:
                    self.on_event(
                        "tool_finished",
                        {
                            "call_id": call_id,
                            "tool": name,
                            "error_code": self.blocked
                            or (
                                "invalid_response" if name == "submit_diagnosis" else "tool_failed"
                            ),
                            "duration_ms": int((time.monotonic() - started) * 1000),
                        },
                    )
                raise
            if self.on_event:
                recorded_arguments = next(
                    (
                        c["arguments"]
                        for c in reversed(self.calls)
                        if c.get("evidence_id") == result["id"]
                    ),
                    safe_arguments,
                )
                self.on_event(
                    "tool_finished",
                    {
                        "call_id": call_id,
                        "tool": name,
                        "result": result,
                        "arguments": recorded_arguments,
                        "internal": internal,
                        "duration_ms": int((time.monotonic() - started) * 1000),
                        "knowledge": self.knowledge,
                    },
                )
            return result

    def _call(self, name, arguments, internal):
        if not internal and len(self.calls) >= 12:
            self.blocked = "tool_limit_exceeded"
            raise ValueError("Tool call limit exceeded")
        if name not in self.definitions:
            self.blocked = "tool_not_allowed"
            raise ValueError("Tool is not allowed")
        body = self.definitions[name].model_validate(arguments)
        with SessionLocal() as db:
            incident, row = _repository.DiagnosticTools__call_query(db, self)
            if (
                row.status != "running"
                or incident.deleted_at
                or incident.status in ("closed", "merged")
                or not requester_valid(db, row)
            ):
                self.blocked = "incident_changed"
                raise ValueError("Analysis scope is no longer available")
            allowed = scope_ids(db, row.incident_id)
            if hasattr(body, "asset_id") and body.asset_id not in allowed:
                self.blocked = "asset_not_allowed"
                raise ValueError("Asset is outside incident scope")
            try:
                value = self.dispatch(db, row, incident, name, body)
            except PermissionError:
                self.blocked = "source_not_allowed"
                raise ValueError("Source is outside frozen scope") from None
            except HTTPException as exc:
                value = {"error_code": "source_unavailable", "http_status": exc.status_code}
        value = redact(value)
        if len(json.dumps(value, ensure_ascii=False).encode()) > 131072:
            value = {
                "error_code": "evidence_too_large",
                **(
                    {
                        "availability": "unavailable",
                        "reason": "evidence_too_large",
                        "source_refs": value.get("source_refs", []),
                        "source_context_hash": value.get("source_context_hash"),
                    }
                    if name in SOURCE_TOOLS
                    else {}
                ),
            }
        record = {
            "id": "e_" + uuid4().hex,
            "source": name,
            "asset_id": str(body.asset_id) if hasattr(body, "asset_id") else None,
            "observed_at": datetime.now(UTC).isoformat(),
            "data": value,
        }
        if isinstance(value, dict) and value.get("error_code"):
            record["error_code"] = value["error_code"]
        self.evidence.append(record)
        if not internal:
            recorded_arguments = redact(body.model_dump(mode="json"))
            if name == "query_database_readonly":
                recorded_arguments["sql"] = value.get("sql", "[rejected read-only query]")
            self.calls.append(
                {
                    "tool": name,
                    "arguments": recorded_arguments,
                    "evidence_id": record["id"],
                }
            )
        if name in SOURCE_TOOLS and not self.on_event:
            # Keep source fragments if the legacy worker stops before final completion.
            with SessionLocal() as db:
                row = _repository.DiagnosticTools__call_query_2(db, self)
                if row.status == "running":
                    row.evidence, row.tool_calls = self.evidence, self.calls
                    db.commit()
        return record

    def service_context(self, db, incident_id):
        from app.repositories.monitoring.service_monitors import incident_assets, incident_monitors
        from app.services.monitoring.service_monitors import response

        result = []
        for monitor in incident_monitors(db, incident_id):
            containers, endpoints = incident_assets(db, monitor)
            result.append(
                {
                    "monitor": response(monitor).model_dump(mode="json"),
                    "instances": [
                        {
                            "id": str(c.id),
                            "name": c.name,
                            "server_id": str(c.server_id),
                            "application_id": str(c.application_id),
                        }
                        for c in containers
                    ],
                    "llm_dependencies": [{"id": str(e.id), "name": e.name} for e in endpoints],
                    "traffic_source": "business_requests_only",
                }
            )
        return result

    def dispatch(self, db, row, incident, name, body):
        if name in {"search_agent_history", "read_agent_history"}:
            from app.services.knowledge.agent_results import tool_call

            return tool_call(db, row, name, body)
        now = datetime.now(UTC)
        if name == "get_service_metrics":
            from app.services.monitoring.service_monitors import metrics

            return metrics(db, body.asset_id, body.metric, now - timedelta(hours=1), now, 30)
        if name == "get_alert_context":
            servers, containers, endpoints = scope(db, incident.id)
            notes = _repository.DiagnosticTools_dispatch_query(db, incident)
            return {
                "incident_id": str(incident.id),
                "source_context": row.source_context,
                "database_context": row.database_context,
                "service_monitoring": self.service_context(db, incident.id),
                "title": incident.title,
                "status": incident.status,
                "alerts": [
                    {
                        "id": str(e.id),
                        "status": e.status,
                        "severity": e.severity,
                        "starts_at": e.starts_at.isoformat(),
                        "labels": e.labels,
                        "annotations": e.annotations,
                        "rule_snapshot": e.rule_snapshot,
                        "recovery_status": e.recovery_status,
                        "recovery_reason": e.recovery_reason,
                        "recovery_checked_at": e.recovery_checked_at.isoformat()
                        if e.recovery_checked_at
                        else None,
                        "recovery_evidence_at": e.recovery_evidence_at.isoformat()
                        if e.recovery_evidence_at
                        else None,
                    }
                    for e in linked_alerts(db, incident.id)
                ],
                "servers": [{"id": str(s.id), "name": s.name} for s in servers],
                "containers": [
                    {
                        "id": str(c.id),
                        "server_id": str(c.server_id),
                        "name": c.name,
                        "status": c.status,
                        "last_seen_at": c.last_seen_at.isoformat(),
                    }
                    for c in containers
                ],
                "llm_endpoints": [
                    {
                        "id": str(e.id),
                        "last_error_type": e.last_error_type,
                        "last_probe_at": e.last_probe_at.isoformat() if e.last_probe_at else None,
                    }
                    for e in endpoints
                ],
                "notes": [n.content for n in notes],
            }
        if name in SOURCE_TOOLS:
            return dispatch_source(db, row.source_context, name, body)
        if name in DATABASE_TOOLS:
            return dispatch_database(db, row, name, body)
        if name in ("get_server_metrics", "get_container_metrics"):
            if name == "get_container_metrics":
                asset = _repository.DiagnosticTools_dispatch_query_2(body, db)
                if not asset or not body.metric.startswith("container_"):
                    raise ValueError("Invalid container metric")
                server_id, docker_id = asset.server_id, asset.docker_id
            else:
                asset = _repository.DiagnosticTools_dispatch_query_3(body, db)
                if not asset or body.metric.startswith("container_"):
                    raise ValueError("Invalid host metric")
                server_id, docker_id = asset.id, None
            series = metrics_result(
                body.metric, server_id, docker_id, now - timedelta(hours=1), now, 60
            )
            if body.detail == "summary":
                series = summarize_metrics(series)
            return {
                "metric": body.metric,
                "detail": body.detail,
                "start": (now - timedelta(hours=1)).isoformat(),
                "end": now.isoformat(),
                "series": series,
            }
        if name == "get_llm_metrics":
            if _repository.DiagnosticTools_dispatch_query_4(body, db) is None:
                raise ValueError("Invalid LLM asset")
            expression = llm_metric_expression(body.metric, str(body.asset_id))
            return {
                "metric": body.metric,
                "unit": "seconds" if "latency" in body.metric else "ratio",
                "series": prometheus(
                    "/api/v1/query_range",
                    {
                        "query": expression,
                        "start": (now - timedelta(hours=1)).timestamp(),
                        "end": now.timestamp(),
                        "step": 60,
                    },
                ),
            }
        if name in ("get_container_status", "get_recent_logs"):
            container = _repository.DiagnosticTools_dispatch_query_5(body, db)
            if container is None:
                raise ValueError("Invalid container asset")
            credential, server = _repository.DiagnosticTools_dispatch_query_6(container, db)
            if credential and credential.deleted_at:
                credential = None
            result = read_container(
                server, credential, container.docker_id, name == "get_recent_logs"
            )
            if name == "get_container_status":
                return {
                    "inventory_status": container.status,
                    "inventory_at": container.last_seen_at.isoformat(),
                    "ssh": result,
                }
            return result
        if name == "get_predictions":
            servers, containers, endpoints = scope(db, row.incident_id)
            ids = {item.id for item in [*servers, *containers, *endpoints]}
            tasks = _repository.DiagnosticTools_dispatch_query_7(db)
            result = []
            for task in tasks:
                if not ids.intersection({task.server_id, task.container_id, task.llm_endpoint_id}):
                    continue
                last = _repository.DiagnosticTools_dispatch_query_8(db, task)
                if last:
                    from app.services.predictions.tasks import is_fresh

                    result.append(
                        {
                            "id": str(last.id),
                            "metric": task.metric,
                            "fresh": is_fresh(db, last, task),
                            "data_end_at": last.data_end_at.isoformat()
                            if last.data_end_at
                            else None,
                            "quality": last.quality,
                            "horizons": last.horizons,
                            "anomalies": last.anomalies,
                        }
                    )
            return result
        if name == "search_knowledge":
            applications, versions = knowledge_scope(db, row.incident_id)
            found = search(
                db, redact(body.query), body.tag, application_ids=applications, versions=versions
            )
            known = {item["id"] for item in self.knowledge}
            self.knowledge.extend(item for item in found if item["id"] not in known)
            return found
        if name == "list_skills":
            applications, versions = knowledge_scope(db, row.incident_id)
            return [
                {
                    "id": str(s.id),
                    "code": s.code,
                    "version": s.version,
                    "title": s.title,
                    "tags": s.tags,
                }
                for s in _repository.DiagnosticTools_dispatch_query_9(db, applications, versions)
            ]
        if name == "read_skill":
            applications, versions = knowledge_scope(db, row.incident_id)
            skill = _repository.DiagnosticTools_dispatch_query_10(body, db, applications, versions)
            if skill is None:
                return {"error_code": "skill_unavailable"}
            if skill.version != body.version:
                return {"error_code": "skill_version_changed"}
            return {
                "id": str(skill.id),
                "code": skill.code,
                "version": skill.version,
                "title": skill.title,
                "tags": skill.tags,
                "content": skill.content,
            }
        if name == "propose_action_skill":
            return validate_proposal(db, row.incident_id, body.model_dump(mode="json"))
        raise ValueError("Unknown tool")


def knowledge_scope(db, incident_id):
    from app.repositories.improvements import current_versions
    from app.repositories.operations_impact import event_applications

    identifiers = set()
    for event in linked_alerts(db, incident_id):
        identifiers.update(event_applications(db, event))
    return list(identifiers), current_versions(db, identifiers)


def summarize_metrics(series):
    result = []
    for item in series.get("result", []):
        values = item.get("values", [])
        finite = [
            point for point in values if point[1] is not None and math.isfinite(float(point[1]))
        ]
        result.append(
            {
                **{key: value for key, value in item.items() if key != "values"},
                "summary": {
                    "sample_count": len(values),
                    "invalid_sample_count": len(values) - len(finite),
                    "first": values[0] if values else None,
                    "last": values[-1] if values else None,
                    "min": min(finite, key=lambda point: float(point[1])) if finite else None,
                    "max": max(finite, key=lambda point: float(point[1])) if finite else None,
                },
            }
        )
    return {**series, "result": result}
