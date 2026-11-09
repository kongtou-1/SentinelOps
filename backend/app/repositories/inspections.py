from sqlalchemy import String, case, func, or_, select, text, update

from app.models import (
    Application,
    InspectionCheckResult,
    InspectionDataSource,
    InspectionItem,
    InspectionPlan,
    InspectionReview,
    InspectionRun,
    InspectionTrace,
    KnowledgeSkill,
    ServiceDatabase,
)

ACTIVE = ("queued", "collecting", "evaluating")


def summary(db):
    plan_counts = (
        db.execute(
            select(
                func.count(func.distinct(InspectionPlan.application_id)).label("services_covered"),
                func.count().filter(InspectionPlan.enabled.is_(True)).label("plans_enabled"),
            )
            .select_from(InspectionPlan)
            .outerjoin(Application, Application.id == InspectionPlan.application_id)
            .where(
                InspectionPlan.deleted_at.is_(None),
                InspectionPlan.trial_only.is_(False),
                Application.deleted_at.is_(None),
            )
        )
        .mappings()
        .one()
    )
    run_counts = (
        db.execute(
            select(
                func.count().label("runs_total"),
                func.count().filter(InspectionRun.status.in_(ACTIVE)).label("runs_active"),
            )
            .select_from(InspectionRun)
            .where(InspectionRun.deleted_at.is_(None), InspectionRun.run_kind == "standard")
        )
        .mappings()
        .one()
    )
    item_counts = (
        db.execute(
            select(
                func.count().label("items_total"),
                func.count().filter(outcome_column() == "fail").label("issues_total"),
            )
            .select_from(InspectionItem)
            .join(InspectionRun, InspectionRun.id == InspectionItem.run_id)
            .where(
                InspectionItem.deleted_at.is_(None),
                InspectionRun.deleted_at.is_(None),
                InspectionRun.run_kind == "standard",
            )
        )
        .mappings()
        .one()
    )
    return {**plan_counts, **run_counts, **item_counts}


def plans(application_id=None, search=None, enabled=None, environment=None):
    query = select(InspectionPlan).where(
        InspectionPlan.deleted_at.is_(None), InspectionPlan.trial_only.is_(False)
    )
    if application_id:
        query = query.where(InspectionPlan.application_id == application_id)
    if search:
        query = query.where(InspectionPlan.name.icontains(search, autoescape=True))
    if enabled is not None:
        query = query.where(InspectionPlan.enabled == enabled)
    if environment:
        query = query.where(InspectionPlan.environment == environment)
    return query.order_by(
        InspectionPlan.application_id.nulls_last(),
        InspectionPlan.environment,
        InspectionPlan.name,
        InspectionPlan.id,
    )


def plan_group_counts(db, application_id=None, search=None, enabled=None, environment=None):
    return {
        (app_id, env): count
        for app_id, env, count in db.execute(
            plans(application_id, search, enabled, environment)
            .with_only_columns(
                InspectionPlan.application_id, InspectionPlan.environment, func.count()
            )
            .order_by(None)
            .group_by(InspectionPlan.application_id, InspectionPlan.environment)
        )
    }


def plan(db, identifier, lock=False):
    query = select(InspectionPlan).where(
        InspectionPlan.id == identifier, InspectionPlan.deleted_at.is_(None)
    )
    if lock:
        query = query.with_for_update()
    return db.scalar(query.execution_options(populate_existing=True))


def application(db, identifier):
    return db.get(Application, identifier, populate_existing=True)


def application_names(db, identifiers):
    return dict(
        db.execute(
            select(Application.id, Application.name).where(
                Application.id.in_(identifiers), Application.deleted_at.is_(None)
            )
        ).all()
    )


def skill(db, identifier):
    return db.get(KnowledgeSkill, identifier, populate_existing=True) if identifier else None


def active_run(db, plan_id):
    return db.scalar(
        select(InspectionRun).where(
            InspectionRun.plan_id == plan_id,
            InspectionRun.status.in_(ACTIVE),
            InspectionRun.deleted_at.is_(None),
        )
    )


def due(db, now):
    return db.scalars(
        select(InspectionPlan)
        .where(
            InspectionPlan.deleted_at.is_(None),
            InspectionPlan.enabled.is_(True),
            InspectionPlan.next_run_at <= now,
            InspectionPlan.id.not_in(
                select(InspectionRun.plan_id).where(
                    InspectionRun.status.in_(ACTIVE), InspectionRun.deleted_at.is_(None)
                )
            ),
        )
        .order_by(InspectionPlan.next_run_at, InspectionPlan.id)
        .limit(10)
        .with_for_update(skip_locked=True)
    ).all()


def runs(plan_id=None, search=None, status=None, run_kind=None, has_objects=None):
    query = select(InspectionRun).where(InspectionRun.deleted_at.is_(None))
    if plan_id:
        query = query.where(InspectionRun.plan_id == plan_id)
    if status:
        query = query.where(InspectionRun.status == status)
    if run_kind:
        query = query.where(InspectionRun.run_kind == run_kind)
    if has_objects is not None:
        query = query.where(
            InspectionRun.collected > 0 if has_objects else InspectionRun.collected == 0
        )
    if search:
        query = query.where(
            InspectionRun.snapshot["plan"]["name"].as_string().icontains(search, autoescape=True)
        )
    return query.order_by(InspectionRun.created_at.desc(), InspectionRun.id)


def run(db, identifier, lock=False):
    query = select(InspectionRun).where(
        InspectionRun.id == identifier, InspectionRun.deleted_at.is_(None)
    )
    if lock:
        query = query.with_for_update()
    return db.scalar(query.execution_options(populate_existing=True))


def delete_run_items(db, run_id, deleted_at):
    return db.execute(
        update(InspectionItem)
        .where(InspectionItem.run_id == run_id, InspectionItem.deleted_at.is_(None))
        .values(deleted_at=deleted_at, updated_at=deleted_at)
    ).rowcount


def runnable(db):
    from datetime import UTC, datetime

    return db.scalar(
        select(InspectionRun)
        .where(
            InspectionRun.status.in_(ACTIVE),
            InspectionRun.deleted_at.is_(None),
            (InspectionRun.retry_at.is_(None)) | (InspectionRun.retry_at <= datetime.now(UTC)),
        )
        .order_by(InspectionRun.created_at, InspectionRun.id)
        .limit(1)
    )


def items(run_id, verdict=None, search=None, result=None):
    query = select(InspectionItem).where(
        InspectionItem.run_id == run_id, InspectionItem.deleted_at.is_(None)
    )
    if verdict:
        query = query.where(InspectionItem.verdict == verdict)
    if search:
        facts = InspectionItem.evidence["facts"]
        query = query.where(
            InspectionItem.record_id.icontains(search, autoescape=True)
            | InspectionItem.evidence["facts"].cast(String).icontains(search, autoescape=True)
            | facts["name"].as_string().icontains(search, autoescape=True)
            | facts["title"].as_string().icontains(search, autoescape=True)
            | facts["main"]["name"].as_string().icontains(search, autoescape=True)
            | facts["main"]["title"].as_string().icontains(search, autoescape=True)
        )
    if result:
        query = query.where(outcome_column() == result)
    return query.order_by(
        case((outcome_column() == "fail", 0), (outcome_column() == "unknown", 1), else_=2),
        InspectionItem.created_at,
        InspectionItem.id,
    )


def item_exists(db, identity):
    return db.scalar(select(InspectionItem.id).where(InspectionItem.identity_hash == identity))


def results(plan_id=None, record_id=None, verdict=None, latest_only=True):
    ranked = (
        select(
            InspectionItem.id,
            func.row_number()
            .over(
                partition_by=(InspectionItem.plan_id, InspectionItem.record_id),
                order_by=(InspectionItem.created_at.desc(), InspectionItem.id),
            )
            .label("position"),
        )
        .join(InspectionRun, InspectionRun.id == InspectionItem.run_id)
        .join(InspectionPlan, InspectionPlan.id == InspectionItem.plan_id)
        .where(
            InspectionItem.deleted_at.is_(None),
            InspectionRun.deleted_at.is_(None),
            InspectionRun.run_kind == "standard",
            InspectionPlan.deleted_at.is_(None),
        )
        .subquery()
    )
    identifiers = select(ranked.c.id)
    if latest_only:
        identifiers = identifiers.where(ranked.c.position == 1)
    query = select(InspectionItem).where(InspectionItem.id.in_(identifiers))
    if plan_id:
        query = query.where(InspectionItem.plan_id == plan_id)
    if record_id:
        query = query.where(InspectionItem.record_id.icontains(record_id, autoescape=True))
    if verdict:
        query = query.where(InspectionItem.verdict == verdict)
    return query.order_by(InspectionItem.created_at.desc(), InspectionItem.id)


def connections(db, environment=None):
    query = (
        select(ServiceDatabase)
        .outerjoin(Application)
        .where(
            ServiceDatabase.deleted_at.is_(None),
            ServiceDatabase.enabled.is_(True),
            or_(
                ServiceDatabase.application_id.is_(None),
                (Application.deleted_at.is_(None) & Application.enabled.is_(True)),
            ),
        )
    )
    if environment:
        query = query.where(ServiceDatabase.environment == environment)
    return db.scalars(query.order_by(ServiceDatabase.name, ServiceDatabase.id)).all()


def pending_item(db, run_id):
    return db.scalar(
        items(run_id)
        .where(InspectionItem.agent_status.in_(("pending", "running")))
        .order_by(None)
        .order_by(
            case(
                (InspectionItem.verdict == "contradicted", 0),
                (InspectionItem.verdict == "unassessable", 1),
                else_=2,
            ),
            InspectionItem.created_at,
            InspectionItem.id,
        )
        .limit(1)
    )


def counts(db, run_id):
    return dict(
        db.execute(
            select(InspectionItem.verdict, func.count())
            .where(InspectionItem.run_id == run_id, InspectionItem.deleted_at.is_(None))
            .group_by(InspectionItem.verdict)
        ).all()
    )


def outcome_column():
    return case(
        (InspectionItem.check_status.is_not(None), InspectionItem.check_status),
        (InspectionItem.rule_verdict.in_(("pass", "fail")), InspectionItem.rule_verdict),
        # Agent-only checks use their structured conclusion. Rule checks always win.
        (
            (func.json_array_length(InspectionItem.checks) == 0)
            & (InspectionItem.agent_status == "completed"),
            case(
                (InspectionItem.agent_result["verdict"].as_string() == "supported", "pass"),
                (InspectionItem.agent_result["verdict"].as_string() == "contradicted", "fail"),
                else_="unknown",
            ),
        ),
        else_="unknown",
    )


def check_results(run_id, item_id=None, scope=None):
    query = (
        select(InspectionCheckResult)
        .where(
            InspectionCheckResult.run_id == run_id,
            InspectionCheckResult.deleted_at.is_(None),
        )
        .order_by(
            InspectionCheckResult.object_key,
            InspectionCheckResult.kind.desc(),
            InspectionCheckResult.check_id,
        )
    )
    if item_id:
        query = query.where(InspectionCheckResult.item_id == item_id)
    if scope:
        query = query.where(InspectionCheckResult.scope == scope)
    return query


def check_counts(db, run_id):
    return db.execute(
        select(
            InspectionCheckResult.status,
            InspectionCheckResult.execution_status,
            InspectionCheckResult.scope,
            func.count(),
        )
        .where(InspectionCheckResult.run_id == run_id, InspectionCheckResult.deleted_at.is_(None))
        .group_by(
            InspectionCheckResult.status,
            InspectionCheckResult.execution_status,
            InspectionCheckResult.scope,
        )
    ).all()


def traces(run_id, check_result_id=None):
    query = (
        select(InspectionTrace)
        .where(InspectionTrace.run_id == run_id, InspectionTrace.deleted_at.is_(None))
        .order_by(InspectionTrace.created_at, InspectionTrace.id)
    )
    if check_result_id:
        query = query.where(InspectionTrace.check_result_id == check_result_id)
    return query


def trace_usage(db, result_id):
    return db.execute(
        select(
            func.count(),
            func.coalesce(func.sum(func.octet_length(InspectionTrace.data.cast(String))), 0),
        )
        .select_from(InspectionTrace)
        .where(InspectionTrace.check_result_id == result_id, InspectionTrace.deleted_at.is_(None))
    ).one()


def check_result_map(db, identifiers):
    return {
        row.id: row
        for row in db.scalars(
            select(InspectionCheckResult).where(
                InspectionCheckResult.id.in_(identifiers),
                InspectionCheckResult.deleted_at.is_(None),
            )
        )
    }


def health_counts(db, identifiers):
    outcome = outcome_column()
    rows = db.execute(
        select(InspectionItem.run_id, outcome, func.count())
        .where(InspectionItem.run_id.in_(identifiers), InspectionItem.deleted_at.is_(None))
        .group_by(InspectionItem.run_id, outcome)
    ).all()
    result = {}
    for identifier, status, count in rows:
        result.setdefault(identifier, {})[status] = count
    return result


def latest_runs(db, identifiers):
    return {
        row.plan_id: row
        for row in db.scalars(
            select(InspectionRun)
            .where(
                InspectionRun.plan_id.in_(identifiers),
                InspectionRun.deleted_at.is_(None),
                InspectionRun.run_kind == "standard",
            )
            .distinct(InspectionRun.plan_id)
            .order_by(InspectionRun.plan_id, InspectionRun.created_at.desc(), InspectionRun.id)
        )
    }


def data_sources(search=None, kind=None, http_connection_id=None):
    query = select(InspectionDataSource).where(InspectionDataSource.deleted_at.is_(None))
    if search:
        query = query.where(InspectionDataSource.name.icontains(search, autoescape=True))
    if kind:
        query = query.where(InspectionDataSource.source_kind == kind)
    if http_connection_id:
        query = query.where(
            InspectionDataSource.source_config["http_connection_id"].as_string()
            == str(http_connection_id)
        )
    return query.order_by(InspectionDataSource.created_at.desc(), InspectionDataSource.id)


def source(db, identifier, lock=False):
    query = select(InspectionDataSource).where(
        InspectionDataSource.id == identifier, InspectionDataSource.deleted_at.is_(None)
    )
    if lock:
        query = query.with_for_update()
    return db.scalar(query.execution_options(populate_existing=True))


def source_references(identifier):
    return plans().where(
        (InspectionPlan.data_source_id == identifier)
        | InspectionPlan.agent_source_ids.cast(String).contains(str(identifier), autoescape=True)
        | InspectionPlan.related_sources.cast(String).contains(str(identifier), autoescape=True)
        | InspectionPlan.check_items.cast(String).contains(str(identifier), autoescape=True)
    )


def reviews(item_id):
    return (
        select(InspectionReview)
        .where(InspectionReview.item_id == item_id, InspectionReview.deleted_at.is_(None))
        .order_by(InspectionReview.created_at, InspectionReview.id)
    )


def worker_lock(connection):
    return connection.scalar(text("SELECT pg_try_advisory_lock(100032)"))
