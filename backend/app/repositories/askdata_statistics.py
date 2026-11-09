"""Aggregate reads for ask-data statistics (no user result rows)."""

from __future__ import annotations

from sqlalchemy import func, select

from app.models import (
    AskDataConfiguration,
    AskDataDefinition,
    AskDataEvent,
    AskDataRun,
    ModelUsage,
)


def runs(db, connection_id, start, end):
    query = (
        select(
            AskDataRun.id,
            AskDataRun.status,
            AskDataRun.error_code,
            AskDataRun.created_at,
            func.max(AskDataEvent.created_at).label("last_event_at"),
        )
        .outerjoin(AskDataEvent, AskDataEvent.run_id == AskDataRun.id)
        .where(
            AskDataRun.created_at >= start,
            AskDataRun.created_at < end,
            AskDataRun.deleted_at.is_(None),
        )
        .group_by(AskDataRun.id)
        .execution_options(access_internal=True)
    )
    if connection_id:
        query = query.where(AskDataRun.connection_id == connection_id)
    return db.execute(query).all()


def model_usage(db, connection_id, start, end):
    query = select(
        func.coalesce(func.sum(ModelUsage.calls), 0),
        func.coalesce(func.sum(ModelUsage.input_tokens), 0),
        func.coalesce(func.sum(ModelUsage.output_tokens), 0),
    ).where(
        ModelUsage.source == "askdata",
        ModelUsage.occurred_at >= start,
        ModelUsage.occurred_at < end,
        ModelUsage.deleted_at.is_(None),
    )
    if connection_id:
        query = query.where(ModelUsage.resource_id == connection_id)
    row = db.execute(query.execution_options(access_internal=True)).one()
    return {"calls": int(row[0] or 0), "input_tokens": float(row[1] or 0),
            "output_tokens": float(row[2] or 0)}


def certification(db, connection_id):
    query = (
        select(
            func.count(),
            func.count().filter(AskDataDefinition.certified.is_(True)),
            func.count().filter(AskDataDefinition.review_required.is_(True)),
        )
        .select_from(AskDataDefinition)
        .join(
            AskDataConfiguration,
            AskDataConfiguration.active_model_version_id
            == AskDataDefinition.model_version_id,
        )
        .where(
            AskDataDefinition.deleted_at.is_(None),
            AskDataConfiguration.deleted_at.is_(None),
        )
    )
    if connection_id:
        query = query.where(AskDataDefinition.connection_id == connection_id)
    row = db.execute(query.execution_options(access_internal=True)).one()
    return {
        "total": int(row[0] or 0),
        "certified": int(row[1] or 0),
        "pending_review": int(row[2] or 0),
    }
