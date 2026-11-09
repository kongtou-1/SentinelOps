"""Database access; transaction ownership stays with the caller."""

from sqlalchemy import func, select

from app.models import LLMEndpoint, ModelUsage


def endpoints_query(q):
    return (
        select(LLMEndpoint)
        .where(
            LLMEndpoint.deleted_at.is_(None),
            (
                LLMEndpoint.name.icontains(q or "", autoescape=True)
                | LLMEndpoint.model.icontains(q or "", autoescape=True)
            ),
        )
        .order_by(LLMEndpoint.name)
    )


def delete_endpoint_query():
    return func.clock_timestamp()


def status_counts(db, endpoint, start, end):
    bucket = func.floor(
        (func.extract("epoch", ModelUsage.occurred_at) - start.timestamp()) / 30
    ).label("bucket")
    return db.execute(
        select(
            bucket,
            func.count().label("requests"),
            func.count().filter(ModelUsage.outcome == "success").label("successes"),
            func.max(ModelUsage.occurred_at).label("last_request_at"),
        )
        .where(
            ModelUsage.resource_kind == "llm_endpoint",
            ModelUsage.resource_id == endpoint.id,
            ModelUsage.source == "llm_probe",
            ModelUsage.outcome.in_(("success", "failed")),
            ModelUsage.deleted_at.is_(None),
            ModelUsage.occurred_at >= start,
            ModelUsage.occurred_at >= endpoint.probe_config_changed_at,
            ModelUsage.occurred_at < end,
        )
        .group_by(bucket)
    ).all()
