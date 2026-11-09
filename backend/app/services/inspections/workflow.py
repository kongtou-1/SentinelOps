"""Agent-managed inspections. All IO stays behind plan-bound tools."""

import hashlib
import json
import re
from datetime import UTC, datetime
from urllib.parse import quote

import httpx2
from fastapi import HTTPException

from app.core.secrets import cipher
from app.db.session import SessionLocal
from app.integrations import inspection_python
from app.models import InspectionItem
from app.schemas.diagnostic_tools import Empty
from app.schemas.inspections import (
    InspectionAPIRequest,
    InspectionPythonRequest,
    InspectionSource,
    InspectionWorkflowResult,
)
from app.services.inspections import data_sources
from app.services.inspections.agent import HISTORY_TOOLS, history_call
from app.utils.redaction import redact


def authorized_sources(db, plan):
    result = []
    for identifier in dict.fromkeys([str(plan.data_source_id), *map(str, plan.agent_source_ids)]):
        row = data_sources.active(db, identifier)
        if (
            not row.enabled
            or row.source_kind != "api"
            or row.environment != plan.environment
            or (row.application_id and row.application_id != plan.application_id)
            or not row.source_config.get("agent_api_enabled")
        ):
            raise ValueError("inspection_api_not_authorized")
        data_sources.validate(db, row)
        from app.services.inspections.connections import effective

        result.append(effective(db, row))
    return result


def request_api(row, arguments):
    source = InspectionSource.model_validate(row.source_config)
    names = set(re.findall(r"\{([A-Za-z_]\w*)\}", source.path_template))
    if set(arguments.path_params) != names:
        raise ValueError("inspection_api_path_parameters")
    path = source.path_template
    for name, value in arguments.path_params.items():
        if not value or len(value) > 200 or value in {".", ".."} or re.search(r"[\s/\\%?#]", value):
            raise ValueError("inspection_api_path_parameters")
        path = path.replace("{" + name + "}", quote(value, safe=""))
    if not set(arguments.query) <= set(source.allowed_query_params):
        raise ValueError("inspection_api_query_not_authorized")
    if not set(arguments.body) <= set(source.allowed_body_fields):
        raise ValueError("inspection_api_body_not_authorized")
    if source.request_method == "GET" and arguments.body:
        raise ValueError("inspection_api_body_not_authorized")
    if len(json.dumps(arguments.model_dump(mode="json"), allow_nan=False).encode()) > 16384:
        raise ValueError("inspection_api_input_limit")
    headers = (
        json.loads(cipher().decrypt(row.encrypted_headers.encode()))
        if row.encrypted_headers
        else {}
    )
    url = source.url.rstrip("/") + "/" + path if path else source.url
    params = [(p.name, p.value) for p in source.query_params] + list(arguments.query.items())
    secret_values = set(headers.values())
    for name, value in headers.items():
        if name.lower() != "authorization":
            continue
        scheme, _, credential = value.partition(" ")
        if scheme.lower() == "bearer":
            secret_values.add(credential)
        elif scheme.lower() == "basic":
            import base64
            import binascii

            try:
                decoded = base64.b64decode(credential, validate=True).decode()
                secret_values.update([decoded, decoded.partition(":")[2]])
            except (ValueError, UnicodeError, binascii.Error):
                pass
    try:
        with httpx2.Client(timeout=8, trust_env=False, follow_redirects=False) as client:
            with client.stream(
                source.request_method,
                url,
                headers=headers,
                params=params,
                json=arguments.body if source.request_method == "POST" else None,
            ) as reply:
                if reply.status_code != 200:
                    raise ValueError("inspection_api_http_error")
                raw = bytearray()
                for chunk in reply.iter_bytes():
                    raw.extend(chunk)
                    if len(raw) > 65536:
                        raise ValueError("inspection_api_response_limit")
        # Remove exact configured credential values as well as sensitive field names.
        content = json.loads(raw)

        def clean(value):
            if isinstance(value, dict):
                return {k: clean(v) for k, v in value.items()}
            if isinstance(value, list):
                return [clean(v) for v in value]
            if isinstance(value, str):
                for secret in secret_values:
                    if secret:
                        value = value.replace(secret, "[REDACTED]")
                return value
            return value

        return redact(clean(content))
    except (httpx2.HTTPError, json.JSONDecodeError):
        raise ValueError("inspection_api_unavailable") from None


class WorkflowTools:
    definitions = {
        "read_inspection_skill": Empty,
        "list_inspection_apis": Empty,
        "request_inspection_api": InspectionAPIRequest,
        "execute_inspection_python": InspectionPythonRequest,
        **HISTORY_TOOLS,
    }

    def __init__(self, run_id, item_id):
        import time

        self.run_id, self.item_id = run_id, item_id
        self.started = time.monotonic()
        self.blocked, self.attempts = None, 0

    def guard(self):
        from app.services.inspections.runtime import current_run

        try:
            with SessionLocal() as db:
                current_run(db, self.run_id)
        except (ValueError, HTTPException) as exc:
            self.blocked = "inspection_authorization_changed"
            raise ValueError(self.blocked) from exc

    def call(self, name, arguments):
        from app.services.inspections.runtime import current_run

        if name not in self.definitions or self.attempts >= 64:
            self.blocked = "inspection_tool_limit"
            raise ValueError(self.blocked)
        args = self.definitions[name].model_validate(arguments)
        self.attempts += 1
        self.guard()
        with SessionLocal() as db:
            plan, run = current_run(db, self.run_id)
            item = db.get(InspectionItem, self.item_id)
            if not item or item.deleted_at or item.run_id != run.id:
                raise ValueError("inspection_item_unavailable")
            if name in HISTORY_TOOLS:
                return history_call(db, run, name, args)
            if name == "read_inspection_skill":
                return {
                    **run.snapshot["skill"],
                    "parameters": redact(plan.agent_parameters),
                    "max_records": min(plan.max_records, 200),
                    "window_start": run.window_start.isoformat(),
                    "window_end": run.window_end.isoformat(),
                }
            rows = authorized_sources(db, plan)
            if name == "list_inspection_apis":
                return [
                    {
                        "source_id": str(row.id),
                        "name": row.name,
                        **{
                            key: row.source_config.get(key, default)
                            for key, default in (
                                ("url", ""),
                                ("request_method", "GET"),
                                ("path_template", ""),
                                ("allowed_query_params", []),
                                ("allowed_body_fields", []),
                            )
                        },
                    }
                    for row in rows
                ]
            calls = list(item.evidence.get("tool_calls", []))
            references = {call["id"]: call for call in calls if call["success"]}
            metadata = {"tool": name, "arguments": redact(arguments)}
            try:
                if name == "request_inspection_api":
                    row = next((row for row in rows if row.id == args.source_id), None)
                    if row is None:
                        raise ValueError("inspection_api_not_authorized")
                    value = request_api(row, args)
                    metadata.update(source_id=str(row.id), source_version=row.version)
                else:
                    if not set(args.input_ids) <= references.keys():
                        raise ValueError("inspection_python_invalid_inputs")
                    code = args.code or run.snapshot["skill"].get("scripts", {}).get(args.script)
                    if not code:
                        raise ValueError("inspection_skill_script_unavailable")
                    metadata["code_sha256"] = hashlib.sha256(code.encode()).hexdigest()
                    data = {
                        "inputs": redact(args.inputs),
                        "evidence": {key: references[key]["data"] for key in args.input_ids},
                    }
                    if len(json.dumps(data).encode()) > 160 * 1024:
                        raise ValueError("inspection_python_input_limit")
                    value = inspection_python.execute(code, data)
                success = not (isinstance(value, dict) and value.get("error_code"))
            except ValueError as exc:
                value, success = {"error_code": str(exc)}, False
            self.guard()
            # Lock only while persisting, never during remote IO. Concurrent calls merge evidence.
            plan, run = current_run(db, self.run_id, lock=True)
            db.refresh(item)
            calls = list(item.evidence.get("tool_calls", []))
            entry = {
                **metadata,
                "id": "e" + str(len(calls) + 1),
                "success": success,
                "observed_at": datetime.now(UTC).isoformat(),
                "data": redact(value),
            }
            calls.append(entry)
            if len(json.dumps(calls, allow_nan=False).encode()) > 512 * 1024:
                self.blocked = "inspection_evidence_limit"
                raise ValueError(self.blocked)
            item.evidence = {**item.evidence, "tool_calls": calls}
            db.commit()
            return entry


def validate_result(answer, tools):
    result = InspectionWorkflowResult.model_validate(redact(answer))
    with SessionLocal() as db:
        from app.services.inspections.runtime import current_run

        plan, _ = current_run(db, tools.run_id)
        item = db.get(InspectionItem, tools.item_id)
        calls = item.evidence.get("tool_calls", [])
        valid = {call["id"] for call in calls if call["success"]}
        if not any(c["success"] and c["tool"] == "request_inspection_api" for c in calls):
            raise ValueError("inspection_workflow_no_observation")
        if not set(result.evidence_ids) <= valid:
            raise ValueError("inspection_invalid_evidence")
        if any(not set(f.evidence_ids) <= valid for f in result.findings):
            raise ValueError("inspection_invalid_evidence")
        if len(result.findings) > min(plan.max_records, 200):
            raise ValueError("inspection_workflow_record_limit")
        if len({f.record_id for f in result.findings}) != len(result.findings):
            raise ValueError("inspection_workflow_duplicate_records")
        if result.no_matches and result.findings or not result.findings and not result.no_matches:
            raise ValueError("inspection_workflow_missing_results")
        if any(not call["success"] for call in calls):
            result.complete = False
        return result.model_dump(mode="json")


def persist_findings(db, plan, run, item, result):
    """Keep the orchestration evidence, expose each inspected object in existing results."""
    from app.services.service_databases import digest

    run.summary = {
        "source_complete": result["complete"],
        "workflow_summary": result["summary"],
        "no_matches": result["no_matches"],
    }
    findings = result["findings"]
    if not findings:
        item.record_id = "无匹配项目"
        item.verdict = "unassessable"
        item.agent_result = {**result, "verdict": "unassessable"}
        run.collected = 1
        return
    calls = item.evidence.get("tool_calls", [])
    for finding in findings:
        verdict = {"pass": "supported", "fail": "contradicted", "unknown": "unassessable"}[
            finding["status"]
        ]
        # Include dependencies of referenced Python calls, preserving the source chain.
        ids = set(finding["evidence_ids"])
        for call in reversed(calls):
            if call["id"] in ids:
                ids.update(call.get("arguments", {}).get("input_ids", []))
        evidence = {
            "facts": {"name": finding["record_id"], "summary": finding["summary"]},
            "tool_calls": [c for c in calls if c["id"] in ids],
        }
        db.add(
            InspectionItem(
                run_id=run.id,
                plan_id=plan.id,
                record_id=finding["record_id"],
                record_version=str(plan.version),
                observed_at=datetime.now(UTC),
                identity_hash=digest([str(run.id), finding["record_id"]]),
                evidence=evidence,
                checks=[],
                verdict=verdict,
                rule_verdict=finding["status"],
                agent_status="completed",
                agent_result={**finding, "verdict": verdict},
            )
        )
    item.deleted_at = item.updated_at = datetime.now(UTC)
    run.collected = len(findings)
