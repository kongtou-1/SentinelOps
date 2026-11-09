from sqlalchemy import case, func, select
from sqlalchemy.dialects.postgresql import insert

from app.models import (
    AgentAnalysis,
    Application,
    ModelUsage,
    PostmortemReport,
    SkillExecution,
    SourceSnapshot,
    UsageCursor,
)


def insert_usage(db, values):
    statement = insert(ModelUsage).values(**values)
    return db.scalar(
        statement.on_conflict_do_nothing(index_elements=[ModelUsage.dedupe_key]).returning(
            ModelUsage.id
        )
    )


def usage_by_key(db, key):
    return db.scalar(select(ModelUsage).where(ModelUsage.dedupe_key == key).with_for_update())


def usages(
    start,
    end,
    application_id=None,
    purpose=None,
    source=None,
    agent_module=None,
    agent_channel_id=None,
    model=None,
):
    query = select(ModelUsage).where(
        ModelUsage.occurred_at >= start,
        ModelUsage.occurred_at < end,
        ModelUsage.deleted_at.is_(None),
    )
    if application_id:
        query = query.where(ModelUsage.application_id == application_id)
    if purpose:
        query = query.where(ModelUsage.purpose == purpose)
    if source:
        query = query.where(ModelUsage.source == source)
    if agent_module:
        query = query.where(ModelUsage.agent_module == agent_module)
    if agent_channel_id:
        query = query.where(ModelUsage.agent_channel_id == agent_channel_id)
    if model:
        query = query.where(ModelUsage.model == model)
    return query.order_by(ModelUsage.occurred_at.desc(), ModelUsage.id)


def rows(db, query):
    return db.scalars(query).all()


def applications_due(db, until):
    return db.scalars(
        select(Application)
        .outerjoin(UsageCursor, UsageCursor.application_id == Application.id)
        .where(
            Application.deleted_at.is_(None),
            Application.enabled.is_(True),
            Application.traffic_target.is_not(None),
            (UsageCursor.observed_until.is_(None)) | (UsageCursor.observed_until < until),
        )
        .order_by(UsageCursor.observed_until.asc().nullsfirst(), Application.id)
        .limit(5)
    ).all()


def cursor(db, identifier):
    return db.scalar(
        select(UsageCursor).where(UsageCursor.application_id == identifier).with_for_update()
    )


def metrics_groups(db):
    return db.execute(
        select(
            ModelUsage.model,
            ModelUsage.purpose,
            ModelUsage.outcome,
            func.count(),
            func.sum(ModelUsage.input_tokens),
            func.sum(ModelUsage.output_tokens),
            func.sum(ModelUsage.unknown_calls),
        )
        .where(ModelUsage.source.in_(("platform_gateway", "llm_probe")))
        .group_by(ModelUsage.model, ModelUsage.purpose, ModelUsage.outcome)
    ).all()


def business_counts(db):
    result = []
    for operation, model, success in (
        ("diagnosis", AgentAnalysis, "completed"),
        ("report", PostmortemReport, "approved"),
        ("source_snapshot", SourceSnapshot, "ready"),
        ("execution", SkillExecution, "succeeded"),
    ):
        counts = {"success": 0, "failure": 0}
        for status, count in db.execute(
            select(model.status, func.count())
            .where(model.status.in_((success, "failed", "interrupted", "unknown")))
            .group_by(model.status)
        ):
            counts["success" if status == success else "failure"] += count
        result.extend((operation, outcome, count) for outcome, count in counts.items())
    return result


def histogram_groups(db, field, bounds):
    column = getattr(ModelUsage, field)
    columns = [func.sum(case((column <= bound, 1), else_=0)) for bound in bounds]
    return db.execute(
        select(ModelUsage.model, ModelUsage.purpose, func.count(), func.sum(column), *columns)
        .where(ModelUsage.source.in_(("platform_gateway", "llm_probe")), column.is_not(None))
        .group_by(ModelUsage.model, ModelUsage.purpose)
    ).all()
