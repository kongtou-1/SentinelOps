"""Database access; transaction ownership stays with the caller."""

from sqlalchemy import select

from app.models import LLMEndpoint


def load_endpoint_query(db, endpoint_id):
    endpoint = db.get(LLMEndpoint, endpoint_id)
    return endpoint


def save_result_query(db, result):
    endpoint = db.get(LLMEndpoint, result.endpoint_id)
    return endpoint


def ProbeService_schedule_once_endpoints_query(db):
    return db.scalars(
        select(LLMEndpoint)
        .where(LLMEndpoint.deleted_at.is_(None), LLMEndpoint.probe_enabled.is_(True))
        .order_by(LLMEndpoint.last_probe_at.asc().nullsfirst(), LLMEndpoint.created_at)
    )


def ProbeService_metrics_query(db):
    active = db.scalars(
        select(LLMEndpoint).where(
            LLMEndpoint.deleted_at.is_(None), LLMEndpoint.probe_enabled.is_(True)
        )
    ).all()
    return active
