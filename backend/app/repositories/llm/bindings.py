"""Database access; transaction ownership stays with the caller."""

from sqlalchemy import select

from app.models import Application, LLMEndpoint, LLMServiceBinding, SourceAuditLog


def binding_responses_query(db, rows):
    applications = {
        row.id: row
        for row in db.scalars(
            select(Application).where(Application.id.in_({r.application_id for r in rows}))
        )
    }
    endpoints = {
        row.id: row
        for row in db.scalars(
            select(LLMEndpoint).where(LLMEndpoint.id.in_({r.llm_endpoint_id for r in rows}))
        )
    }
    return applications, endpoints


def bindings_query():
    query = (
        select(LLMServiceBinding)
        .join(Application)
        .join(LLMEndpoint)
        .where(
            LLMServiceBinding.deleted_at.is_(None),
            Application.deleted_at.is_(None),
            LLMEndpoint.deleted_at.is_(None),
        )
        .order_by(Application.name, LLMEndpoint.name, LLMServiceBinding.id)
    )
    return query


def bindings_query_2(application_id, query):
    query = query.where(LLMServiceBinding.application_id == application_id)
    return query


def bindings_query_3(llm_endpoint_id, query):
    query = query.where(LLMServiceBinding.llm_endpoint_id == llm_endpoint_id)
    return query


def lock_targets_query(db, endpoint):
    db.refresh(endpoint, with_for_update=True)


def lock_targets_query_2(application, db):
    db.refresh(application, with_for_update=True)


def audit_query(action, application_id, db, endpoint_id, user):
    db.add(
        SourceAuditLog(
            application_id=application_id,
            user_id=user.id,
            action=action,
            detail={"llm_endpoint_id": str(endpoint_id)},
        )
    )


def bind_query(application_id, db, endpoint_id):
    row = db.scalar(
        select(LLMServiceBinding).where(
            LLMServiceBinding.application_id == application_id,
            LLMServiceBinding.llm_endpoint_id == endpoint_id,
        )
    )
    return row


def unbind_query(application_id, db, endpoint_id):
    row = db.scalar(
        select(LLMServiceBinding).where(
            LLMServiceBinding.application_id == application_id,
            LLMServiceBinding.llm_endpoint_id == endpoint_id,
            LLMServiceBinding.deleted_at.is_(None),
        )
    )
    return row
