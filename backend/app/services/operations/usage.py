import hashlib
import json
import math
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException

from app.db.session import SessionLocal
from app.integrations.prometheus import prometheus
from app.models import (
    AgentAnalysis,
    AgentChannel,
    AgentConfig,
    AnalysisRun,
    EvaluationRun,
    InspectionCheckResult,
    InspectionItem,
    InspectionPlan,
    LLMEndpoint,
    PostmortemReport,
    UsageCursor,
)
from app.repositories import usage as repository
from app.repositories.operations_impact import endpoint_applications, event_applications
from app.services.incidents.logic import linked_alerts
from app.services.pagination import page


def application_for(db, kind, identifier):
    if kind in {"inspection_item", "inspection_check"}:
        item = db.get(
            InspectionItem if kind == "inspection_item" else InspectionCheckResult, identifier
        )
        plan = db.get(InspectionPlan, item.plan_id) if item else None
        return plan.application_id if plan else None
    model = {
        "analysis": AgentAnalysis,
        "analysis_run": AnalysisRun,
        "report": PostmortemReport,
        "evaluation_run": EvaluationRun,
        "llm_endpoint": LLMEndpoint,
    }.get(kind)
    row = db.get(model, identifier) if model else None
    if not row:
        return None
    if kind == "llm_endpoint":
        identifiers = endpoint_applications(db, identifier)
    elif kind == "evaluation_run":
        return None
    else:
        identifiers = set()
        for event in linked_alerts(db, row.incident_id):
            identifiers.update(event_applications(db, event))
    return next(iter(identifiers)) if len(identifiers) == 1 else None


def start_gateway(context, index, model, provider_kind, attempt=None):
    if not context:
        return None
    kind, identifier, purpose = context
    attempt = attempt or {}
    key = f"gateway:{identifier}:{index}"
    if attempt:
        key += f":{attempt['attempt_index']}:{attempt['agent_channel_id']}"
    with SessionLocal() as db:
        channel = db.get(AgentChannel, attempt["agent_channel_id"]) if attempt else None
        repository.insert_usage(
            db,
            {
                "dedupe_key": key,
                "source": "platform_gateway",
                "provider_kind": provider_kind,
                "application_id": application_for(db, kind, identifier),
                "resource_kind": kind,
                "resource_id": identifier,
                "purpose": purpose,
                "operation": purpose,
                "model": model[:100],
                **attempt,
                "occurred_at": datetime.now(UTC),
                "calls": 1,
                "input_tokens": None,
                "output_tokens": None,
                "unknown_calls": 1,
                "outcome": "unknown",
                "detail": {
                    "reason": "response_pending",
                    "channel_name": channel.name if channel else None,
                },
            },
        )
        db.commit()
    return key


def valid_tokens(usage):
    values = [usage.get("prompt_tokens"), usage.get("completion_tokens")]
    return [value if type(value) is int and 0 <= value <= 10**12 else None for value in values]


def finish_gateway(key, usage, duration, ttft, outcome, error=None):
    if not key:
        return
    input_tokens, output_tokens = valid_tokens(usage or {})
    with SessionLocal() as db:
        row = repository.usage_by_key(db, key)
        if not row:
            return
        row.input_tokens, row.output_tokens = input_tokens, output_tokens
        row.unknown_calls = int(input_tokens is None or output_tokens is None)
        row.duration_seconds, row.ttft_seconds, row.outcome = duration, ttft, outcome
        row.detail = {
            **row.detail,
            "reason": "usage_missing" if row.unknown_calls else "provider_reported",
            "estimated": False,
            "error_code": error,
        }
        if row.agent_channel_id:
            channel = db.get(AgentChannel, row.agent_channel_id, with_for_update=True)
            config = db.get(AgentConfig, row.agent_module)
            if channel and not channel.deleted_at and config.version == row.config_version:
                if channel.last_used_at is None or channel.last_used_at <= row.occurred_at:
                    channel.last_used_at = row.occurred_at
                    channel.last_success = outcome == "success"
                    channel.last_error = error
                    channel.last_latency_ms = duration * 1000
        db.commit()


def record_probe(db, endpoint, result):
    from app.core.config import get_settings

    input_tokens, output_tokens = result.input_tokens, result.output_tokens
    repository.insert_usage(
        db,
        {
            "dedupe_key": f"probe:{endpoint.id}:{result.checked_at.isoformat()}",
            "source": "llm_probe",
            "provider_kind": get_settings().ai_provider_kind
            if endpoint.base_url == get_settings().ai_base_url
            else "model",
            "application_id": application_for(db, "llm_endpoint", endpoint.id),
            "resource_kind": "llm_endpoint",
            "resource_id": endpoint.id,
            "purpose": "probe",
            "operation": "llm_probe",
            "model": endpoint.model[:100],
            "occurred_at": result.checked_at,
            "calls": 1,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "unknown_calls": 1 if input_tokens is None or output_tokens is None else 0,
            "duration_seconds": result.latency_ms / 1000 if result.latency_ms is not None else None,
            "outcome": "success" if result.success else "failed",
            "detail": {
                "reason": "usage_missing"
                if input_tokens is None or output_tokens is None
                else "provider_reported",
                "estimated": False,
            },
        },
    )


def listing(
    db,
    response,
    start,
    end,
    application_id=None,
    purpose=None,
    source=None,
    offset=0,
    limit=50,
    agent_module=None,
    agent_channel_id=None,
    model=None,
):
    validate_range(start, end)
    rows = page(
        db,
        repository.usages(
            start, end, application_id, purpose, source, agent_module, agent_channel_id, model
        ),
        response,
        offset,
        limit,
    )
    return [
        {
            "id": row.id,
            "source": row.source,
            "provider_kind": row.provider_kind,
            "application_id": row.application_id,
            "resource_kind": row.resource_kind,
            "resource_id": row.resource_id,
            "purpose": row.purpose,
            "operation": row.operation,
            "model": row.model,
            "agent_module": row.agent_module,
            "agent_channel_id": row.agent_channel_id,
            "config_version": row.config_version,
            "request_index": row.request_index,
            "attempt_index": row.attempt_index,
            "duration_seconds": row.duration_seconds,
            "ttft_seconds": row.ttft_seconds,
            "occurred_at": row.occurred_at,
            "calls": row.calls,
            "input_tokens": row.input_tokens,
            "output_tokens": row.output_tokens,
            "unknown_calls": row.unknown_calls,
            "outcome": row.outcome,
            "detail": row.detail,
        }
        for row in rows
    ]


def validate_range(start, end):
    if start >= end or end - start > timedelta(days=366):
        raise HTTPException(422, "Select a range of up to 366 days")


def summary(
    db,
    start,
    end,
    application_id=None,
    purpose=None,
    agent_module=None,
    agent_channel_id=None,
    model=None,
):
    validate_range(start, end)
    groups = {}
    for row in repository.rows(
        db,
        repository.usages(
            start, end, application_id, purpose, None, agent_module, agent_channel_id, model
        ),
    ):
        key = (
            row.purpose,
            row.source,
            row.provider_kind,
            row.agent_module,
            row.agent_channel_id,
            row.model,
        )
        value = groups.setdefault(
            key,
            {
                "purpose": row.purpose,
                "source": row.source,
                "provider_kind": row.provider_kind,
                "agent_module": row.agent_module,
                "agent_channel_id": row.agent_channel_id,
                "channel_name": row.detail.get("channel_name"),
                "model": row.model,
                "calls": 0,
                "success_calls": 0,
                "failed_calls": 0,
                "fallback_attempts": 0,
                "duration_seconds": 0,
                "known_input_tokens": 0,
                "known_output_tokens": 0,
                "unknown_usage_records": 0,
            },
        )
        value["calls"] += row.calls or 0
        if row.outcome in ("success", "failed"):
            value["success_calls" if row.outcome == "success" else "failed_calls"] += row.calls or 0
        value["fallback_attempts"] += int((row.attempt_index or 1) > 1)
        value["duration_seconds"] += row.duration_seconds or 0
        value["known_input_tokens"] += row.input_tokens or 0
        value["known_output_tokens"] += row.output_tokens or 0
        if row.input_tokens is None or row.output_tokens is None or row.unknown_calls:
            value["unknown_usage_records"] += 1
    return {
        "start": start,
        "end": end,
        "groups": list(groups.values()),
    }


def collect_applications(db, now=None):
    now = now or datetime.now(UTC)
    until = datetime.fromtimestamp(int((now - timedelta(seconds=60)).timestamp()) // 300 * 300, UTC)
    for app in repository.applications_due(db, until):
        cursor = repository.cursor(db, app.id)
        epoch = app.traffic_config_changed_at.isoformat()
        start = max(
            cursor.observed_until
            if cursor and cursor.source_epoch == epoch
            else until - timedelta(seconds=300),
            datetime.fromtimestamp(
                int(app.traffic_config_changed_at.timestamp()) // 300 * 300, UTC
            ),
        )
        end = min(start + timedelta(seconds=300), until)
        if start >= end:
            continue
        labels = (
            f'application_id="{app.id}",source_epoch={json.dumps(epoch)},'
            'traffic="business",usage_source!="platform_gateway"'
        )
        records = {}
        try:
            for metric in ("tokens", "usage_observations", "requests"):
                query = (
                    "sum by (model,operation,kind,availability,outcome) "
                    f"(increase(sentinelops_llm_{metric}_total{{{labels}}}"
                    f"[{int((end - start).total_seconds())}s]))"
                )
                data = prometheus(
                    "/api/v1/query", {"query": query, "time": end.timestamp()}, timeout_seconds=2
                )
                for series in data.get("result", []):
                    tags = series.get("metric", {})
                    model, operation = (
                        tags.get("model", "unknown")[:100],
                        tags.get("operation", "business")[:100],
                    )
                    value = float(series["value"][1])
                    if not math.isfinite(value) or value < 0:
                        continue
                    entry = records.setdefault(
                        (model, operation),
                        {
                            "input_tokens": None,
                            "output_tokens": None,
                            "known": 0,
                            "unknown": 0,
                            "calls": 0,
                        },
                    )
                    if metric == "tokens" and tags.get("kind") in ("input", "output"):
                        entry[tags["kind"] + "_tokens"] = value
                    elif metric == "usage_observations":
                        entry["known" if tags.get("availability") == "known" else "unknown"] += (
                            value
                        )
                    elif metric == "requests":
                        entry["calls"] += value
        except (HTTPException, ValueError, KeyError, TypeError):
            records = {}
        if not records:
            records = {
                ("unknown", "business"): {
                    "input_tokens": None,
                    "output_tokens": None,
                    "known": 0,
                    "unknown": None,
                    "calls": None,
                }
            }
        # The source epoch is rechecked after network work; old and new collections cannot mix.
        db.refresh(app)
        if app.deleted_at or not app.enabled or app.traffic_config_changed_at.isoformat() != epoch:
            db.rollback()
            continue
        for (model, operation), values in records.items():
            unknown = values["unknown"]
            if not values["known"]:
                values["input_tokens"] = values["output_tokens"] = None
            label_hash = hashlib.sha256(json.dumps([model, operation]).encode()).hexdigest()
            repository.insert_usage(
                db,
                {
                    "dedupe_key": f"app:{app.id}:{epoch}:{int(end.timestamp())}:{label_hash}",
                    "source": "application_metrics",
                    "provider_kind": "application",
                    "application_id": app.id,
                    "resource_kind": "application",
                    "resource_id": app.id,
                    "purpose": "business",
                    "operation": operation,
                    "model": model,
                    "occurred_at": end,
                    "calls": values["calls"],
                    "input_tokens": values["input_tokens"],
                    "output_tokens": values["output_tokens"],
                    "unknown_calls": unknown,
                    "outcome": "observed",
                    "detail": {
                        "estimated": True,
                        "window_start": start.isoformat(),
                        "window_end": end.isoformat(),
                        "source_epoch": epoch,
                        "reason": "usage_missing"
                        if values["input_tokens"] is None
                        else "application_reported",
                    },
                },
            )
        if not cursor:
            cursor = UsageCursor(application_id=app.id, source_epoch=epoch, observed_until=end)
            db.add(cursor)
        else:
            cursor.source_epoch, cursor.observed_until = epoch, end
        db.commit()
