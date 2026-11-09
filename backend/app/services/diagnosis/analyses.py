import hashlib
import json
from datetime import UTC, datetime, timedelta

import app.repositories.diagnosis.analyses as _repository
from app.models import (
    AgentAnalysis,
    Incident,
)
from app.schemas.diagnosis import Diagnosis, Proposal
from app.services.incidents.logic import append_timeline, linked_alerts, lock_incidents, writable
from app.services.records import active_record


def configuration(module="diagnosis"):
    from app.services.agent_config import profile

    return profile(module)


def available(module="diagnosis"):
    from app.services.agent_config import available as ensure_available

    ensure_available(module)


def scope(db, incident_id):
    events = linked_alerts(db, incident_id)
    server_ids = {item.server_id for item in events if item.server_id}
    endpoint_ids = {item.llm_endpoint_id for item in events if item.llm_endpoint_id}
    from app.repositories.monitoring.service_monitors import incident_assets, incident_monitors

    for monitor in incident_monitors(db, incident_id):
        related_containers, related_endpoints = incident_assets(db, monitor)
        server_ids.update(c.server_id for c in related_containers)
        endpoint_ids.update(e.id for e in related_endpoints)
    for item in events:
        if item.prediction_task_id:
            task = _repository.scope_query(db, item)
            if task and not task.deleted_at:
                if task.server_id:
                    server_ids.add(task.server_id)
                if task.llm_endpoint_id:
                    endpoint_ids.add(task.llm_endpoint_id)
                if task.container_id:
                    container = _repository.scope_query_2(db, task)
                    if container and not container.deleted_at:
                        server_ids.add(container.server_id)
    servers = _repository.scope_query_3(db, server_ids)
    containers, endpoints = _repository.scope_query_4(db, endpoint_ids, server_ids)
    return servers, containers, endpoints


def fingerprint(db, incident, source_context=None, database_context=None):
    from app.services.database_diagnosis import freeze_database_context
    from app.services.sources.diagnostics import freeze_source_context

    servers, containers, endpoints = scope(db, incident.id)
    payload = {
        "incident": [str(incident.id), incident.status, incident.title],
        "config": configuration(),
        "database_context": database_context
        if database_context is not None
        else freeze_database_context(db, incident.id),
        "source_context": source_context
        if source_context is not None
        else freeze_source_context(db, incident.id),
        "alerts": sorted(
            (str(e.id), e.updated_at.isoformat()) for e in linked_alerts(db, incident.id)
        ),
        "assets": sorted(
            (str(r.id), r.updated_at.isoformat()) for r in [*servers, *containers, *endpoints]
        ),
        "knowledge": sorted(
            (str(r.id), r.version, r.enabled, r.index_status)
            for r in _repository.fingerprint_query(db)
        ),
        "skills": sorted(
            (str(r.id), r.version, r.enabled) for r in _repository.fingerprint_query_2(db)
        ),
        "notes": sorted(
            (str(r.id), r.created_at.isoformat())
            for r in _repository.fingerprint_query_3(db, incident)
        ),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def scope_ids(db, incident_id):
    from app.repositories.monitoring.service_monitors import incident_monitors

    servers, containers, endpoints = scope(db, incident_id)
    return {item.id for item in [*servers, *containers, *endpoints]} | {
        monitor.id for monitor in incident_monitors(db, incident_id)
    }


def latest(db, incident_id):
    return _repository.latest_query(db, incident_id)


def enqueue(db, incident_id, user, force, *, trigger="manual", commit=True):
    available()
    lock_incidents(db)
    incident = active_record(db, Incident, incident_id)
    writable(incident)
    active = _repository.enqueue_query(db, incident_id)
    if active:
        return active, False
    from app.services.sources.diagnostics import freeze_source_context

    source_context = freeze_source_context(db, incident.id)
    from app.services.database_diagnosis import freeze_database_context

    database_context = freeze_database_context(db, incident.id)
    digest = fingerprint(db, incident, source_context, database_context)
    previous = latest(db, incident_id)
    if (
        not force
        and not database_context.get("connections")
        and previous
        and previous.status == "completed"
        and previous.input_hash == digest
        and datetime.now(UTC) - previous.finished_at < timedelta(minutes=5)
    ):
        return previous, False
    row = AgentAnalysis(
        incident_id=incident_id,
        requested_by=user.id,
        trigger=trigger,
        input_hash=digest,
        config=configuration(),
        source_context=source_context,
        database_context=database_context,
    )
    _repository.enqueue_query_2(db, row)
    append_timeline(
        db,
        incident,
        "analysis_queued",
        {"analysis_id": str(row.id), "trigger": trigger, "authorized_by": str(user.id)},
        actor=user if trigger == "manual" else None,
    )
    if commit:
        db.commit()
        _repository.enqueue_query_3(db, row)
    return row, True


def requester_valid(db, row):
    if getattr(row, "trigger", "manual") == "automatic":
        from app.services.diagnosis.policy import automatic_authorized

        return automatic_authorized(db, row)
    from app.repositories.access import active_user
    from app.services.access_control import Authorization, bind, record_allowed

    user = active_user(db, row.requested_by)
    if user is None or not Authorization(db, user).has("analyses:run"):
        return False
    if not record_allowed(db, user, Incident, row.incident_id, "analyses:run"):
        return False
    if not record_allowed(db, user, type(row), row.id, "analyses:run"):
        return False
    bind(db, user, "analyses:run")
    return True


def validate_proposal(db, incident_id, value):
    proposal = Proposal.model_validate(value)
    _, containers, _ = scope(db, incident_id)
    if proposal.parameters.container_id not in {item.id for item in containers}:
        raise ValueError("Proposal target is outside incident scope")
    return proposal.model_dump(mode="json")


class InvalidDiagnosisReferences(ValueError):
    def __init__(self, evidence_ids, knowledge_ids):
        super().__init__("Unverified evidence citation")
        self.invalid_evidence_ids = sorted(evidence_ids)
        self.invalid_knowledge_ids = sorted(knowledge_ids)


def validate_result(db, row, answer, evidence, knowledge):
    from app.services.database_diagnosis import database_evidence_valid
    from app.services.sources.diagnostics import diagnosis_gaps, source_evidence_valid

    result = Diagnosis.model_validate(answer)
    if row.config.get("diagnosis_mode") == "read_only" and result.proposal is not None:
        raise ValueError("Read-only diagnosis cannot contain an action proposal")
    context = row.source_context
    allowed = {str(identifier) for identifier in scope_ids(db, row.incident_id)}
    known = {
        item["id"]
        for item in evidence
        if not item.get("error_code")
        and (not item.get("asset_id") or item["asset_id"] in allowed)
        and item.get("source")
        not in (
            "list_skills",
            "read_skill",
            "search_agent_history",
            "read_agent_history",
        )
        and not (
            isinstance(item.get("data"), dict) and item["data"].get("availability") == "unavailable"
        )
        and source_evidence_valid(db, context, item)
        and database_evidence_valid(db, row.database_context, item, run_started_at=row.started_at)
    }
    references = set(result.evidence_ids)
    for cause in result.possible_causes:
        references.update(cause.evidence_ids)
    invalid_evidence = references - known
    invalid_knowledge = set(result.knowledge_ids) - {item["id"] for item in knowledge}
    if invalid_evidence or invalid_knowledge:
        raise InvalidDiagnosisReferences(invalid_evidence, invalid_knowledge)
    if result.proposal:
        validate_proposal(db, row.incident_id, result.proposal.model_dump(mode="json"))
    failures = []
    for item in evidence:
        data = item.get("data")
        error = item.get("error_code")
        if isinstance(data, dict):
            error = error or data.get("error_code")
            if isinstance(data.get("ssh"), dict):
                error = error or data["ssh"].get("error_code")
        if error:
            failures.append(
                f"{item.get('source', 'tool')}（证据 {item['id']}，"
                f"目标 {item.get('asset_id') or '事故'}）：{error}，相应观测未获取。"
            )
    result.data_gaps = list(
        dict.fromkeys(
            [
                *diagnosis_gaps(context, evidence),
                *row.database_context.get("data_gaps", []),
                *failures,
                *result.data_gaps,
            ]
        )
    )[:30]
    return result.model_dump(mode="json")


def complete(db, run_id, answer, evidence, knowledge, calls, error=None):
    from app.utils.redaction import redact

    lock_incidents(db)
    incident, row = _repository.complete_query(db, run_id)
    if incident.deleted_at or incident.status in ("closed", "merged"):
        error = "incident_changed"
    if not requester_valid(db, row):
        error = "requester_unavailable"
    if not error:
        try:
            row.result = redact(validate_result(db, row, answer, evidence, knowledge))
        except (ValueError, TypeError):
            error = "invalid_response"
    row.evidence, row.knowledge, row.tool_calls = redact(evidence), redact(knowledge), redact(calls)
    row.error_code, row.status = error, "failed" if error else "completed"
    row.finished_at = datetime.now(UTC)
    if incident.status not in ("closed", "merged") and not incident.deleted_at:
        append_timeline(
            db,
            incident,
            "analysis_failed" if error else "analysis_completed",
            {"analysis_id": str(row.id), "error_code": error},
        )
    db.commit()


def interrupt_runs(db):
    lock_incidents(db)
    for row in _repository.interrupt_runs_query(db):
        row.status, row.error_code = "interrupted", "worker_interrupted"
        row.finished_at = datetime.now(UTC)
        incident = _repository.interrupt_runs_query_2(db, row)
        if incident.status not in ("closed", "merged"):
            append_timeline(
                db,
                incident,
                "analysis_failed",
                {"analysis_id": str(row.id), "error_code": row.error_code},
            )
    for document in _repository.interrupt_runs_query_3(db):
        document.index_status = "queued"
    db.commit()
