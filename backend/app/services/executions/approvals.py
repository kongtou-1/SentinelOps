import app.repositories.executions.approvals as _repository
from app.models import Approval, AuditLog, Incident, SkillExecution
from app.schemas.executions import AuditResponse
from app.services.executions.actions import create_approval, decide, enqueue
from app.services.executions.skills import RESTART
from app.services.pagination import page, time_range
from app.services.records import active_record


def skills(user):
    return [RESTART]


def request_approval(incident_id, body, request, db, user):
    return create_approval(db, incident_id, body.analysis_id, user, request)


def incident_rows(db, model, incident_id, offset, limit, response):
    active_record(db, Incident, incident_id)
    return page(
        db,
        _repository.incident_rows_query(incident_id, model),
        response,
        offset,
        limit,
    )


def approvals(response, incident_id, db, user, offset=0, limit=50):
    return incident_rows(db, Approval, incident_id, offset, limit, response)


def approval(identifier, db, user):
    return active_record(db, Approval, identifier)


def approve(identifier, body, request, db, user):
    return decide(db, identifier, user, True, body.comment, request)


def reject(identifier, body, request, db, user):
    return decide(db, identifier, user, False, body.comment, request)


def execute(identifier, request, db, user, body=None):
    return enqueue(db, identifier, user, request)


def executions(response, incident_id, db, user, offset=0, limit=50):
    return incident_rows(db, SkillExecution, incident_id, offset, limit, response)


def execution(identifier, db, user):
    return active_record(db, SkillExecution, identifier)


def audits(
    response,
    db,
    user,
    incident_id=None,
    approval_id=None,
    execution_id=None,
    report_id=None,
    user_id=None,
    action=None,
    start=None,
    end=None,
    offset=0,
    limit=50,
    q=None,
):
    query = _repository.audits_query()
    for field, value in (
        ("incident_id", incident_id),
        ("approval_id", approval_id),
        ("execution_id", execution_id),
        ("report_id", report_id),
        ("user_id", user_id),
    ):
        if value is not None:
            query = _repository.audits_query_2(field, query, value)
    query = time_range(query, AuditLog.created_at, start, end)
    if action:
        query = _repository.audits_query_3(action, query)
    if q and q.strip():
        query = _repository.audit_incident_search(query, q.strip())
    rows = page(db, _repository.audits_query_4(query), response, offset, limit)
    users, incidents = _repository.audit_names(db, rows)
    return [
        AuditResponse.model_validate(row).model_copy(
            update={
                "actor_username": users.get(row.user_id),
                "incident_title": incidents.get(row.incident_id),
            }
        )
        for row in rows
    ]
