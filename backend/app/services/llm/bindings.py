"""Shared LLM dependencies and per-service use notes; no deployment or probe changes."""

from datetime import UTC, datetime

import app.repositories.llm.bindings as _repository
from app.models import Application, LLMEndpoint, LLMServiceBinding
from app.schemas.llm import LLMBindingResponse
from app.services.llm.status import endpoint_status
from app.services.pagination import page
from app.services.records import active_record, save


def binding_responses(db, rows):
    applications, endpoints = _repository.binding_responses_query(db, rows)
    return [
        LLMBindingResponse(
            id=row.id,
            application_id=row.application_id,
            application_name=applications[row.application_id].name,
            llm_endpoint_id=row.llm_endpoint_id,
            endpoint_name=endpoints[row.llm_endpoint_id].name,
            model=endpoints[row.llm_endpoint_id].model,
            status=endpoint_status(endpoints[row.llm_endpoint_id]),
            purpose=row.purpose,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )
        for row in rows
    ]


def bindings(response, db, user, application_id=None, llm_endpoint_id=None, offset=0, limit=50):
    query = _repository.bindings_query()
    if application_id:
        active_record(db, Application, application_id)
        query = _repository.bindings_query_2(application_id, query)
    if llm_endpoint_id:
        active_record(db, LLMEndpoint, llm_endpoint_id)
        query = _repository.bindings_query_3(llm_endpoint_id, query)
    return binding_responses(db, page(db, query, response, offset, limit))


def lock_targets(db, endpoint_id, application_id):
    endpoint = active_record(db, LLMEndpoint, endpoint_id)
    _repository.lock_targets_query(db, endpoint)
    application = active_record(db, Application, application_id)
    _repository.lock_targets_query_2(application, db)
    # Refresh after locking so a concurrent soft deletion cannot be bypassed.
    active_record(db, LLMEndpoint, endpoint_id)
    active_record(db, Application, application_id)


def audit(db, application_id, endpoint_id, user, action):
    _repository.audit_query(action, application_id, db, endpoint_id, user)


def bind(endpoint_id, application_id, body, db, user):
    lock_targets(db, endpoint_id, application_id)
    row = _repository.bind_query(application_id, db, endpoint_id)
    if row is None:
        row = LLMServiceBinding(application_id=application_id, llm_endpoint_id=endpoint_id)
    row.purpose = body.purpose.strip()
    row.deleted_at = None
    row.updated_at = datetime.now(UTC)
    audit(db, application_id, endpoint_id, user, "llm_service_binding_saved")
    row = save(db, row)
    return binding_responses(db, [row])[0]


def unbind(endpoint_id, application_id, db, user):
    lock_targets(db, endpoint_id, application_id)
    row = _repository.unbind_query(application_id, db, endpoint_id)
    if row is not None:
        row.deleted_at = row.updated_at = datetime.now(UTC)
        audit(db, application_id, endpoint_id, user, "llm_service_binding_removed")
        db.commit()
