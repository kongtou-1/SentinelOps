"""Scoped projections of existing results, without copying evidence into a second archive."""

from sqlalchemy import String, case, cast, func, literal, or_, select, text, union_all

from app.models import (
    AgentAnalysis,
    AnalysisMessage,
    AnalysisRun,
    Incident,
    InspectionCheckResult,
    InspectionItem,
    InspectionPlan,
    InspectionRun,
    InspectionTrace,
    KnowledgeDocument,
)

MODELS = {
    "diagnosis": AgentAnalysis,
    "conversation": AnalysisRun,
    "inspection_item": InspectionItem,
    "inspection_check": InspectionCheckResult,
}


def projection(model, kind, scope, run, title, result, outcome, summary=None, search_text=None):
    return select(
        literal(kind).label("kind"),
        model.id,
        scope.label("scope_id"),
        run.label("run_id"),
        title.label("title"),
        func.left(
            func.coalesce(
                summary if summary is not None else result["summary"].as_string(),
                "",
            ),
            500,
        ).label("summary"),
        outcome.label("outcome"),
        model.created_at,
        (search_text if search_text is not None else cast(result, String)).label("search_text"),
    ).where(model.deleted_at.is_(None))


def history_query(business, scope_id, conversation_id=None, run_id=None, exclude_id=None):
    if business == "diagnosis":
        queries = [
            projection(
                AgentAnalysis,
                "diagnosis",
                AgentAnalysis.incident_id,
                AgentAnalysis.id,
                Incident.title,
                AgentAnalysis.result,
                literal("retained"),
            )
            .join(Incident, Incident.id == AgentAnalysis.incident_id)
            .where(
                Incident.deleted_at.is_(None),
                AgentAnalysis.incident_id == scope_id,
                AgentAnalysis.status == "completed",
            )
        ]
        if conversation_id:
            queries.append(
                projection(
                    AnalysisRun,
                    "conversation",
                    AnalysisRun.incident_id,
                    AnalysisRun.id,
                    Incident.title,
                    AnalysisRun.candidate,
                    case(
                        (AnalysisRun.candidate["summary"].as_string().is_not(None), "candidate"),
                        else_="conversation",
                    ),
                    summary=func.coalesce(
                        AnalysisRun.candidate["summary"].as_string(),
                        select(AnalysisMessage.content)
                        .where(
                            AnalysisMessage.run_id == AnalysisRun.id,
                            AnalysisMessage.deleted_at.is_(None),
                            AnalysisMessage.status.in_(("accepted", "committed")),
                        )
                        .order_by(AnalysisMessage.created_at.desc(), AnalysisMessage.id.desc())
                        .limit(1)
                        .correlate(AnalysisRun)
                        .scalar_subquery(),
                    ),
                    search_text=cast(AnalysisRun.candidate, String)
                    + func.coalesce(
                        select(func.string_agg(AnalysisMessage.content, "\n"))
                        .where(
                            AnalysisMessage.run_id == AnalysisRun.id,
                            AnalysisMessage.deleted_at.is_(None),
                            AnalysisMessage.status.in_(("accepted", "committed")),
                        )
                        .correlate(AnalysisRun)
                        .scalar_subquery(),
                        "",
                    ),
                )
                .join(Incident, Incident.id == AnalysisRun.incident_id)
                .where(
                    Incident.deleted_at.is_(None),
                    AnalysisRun.incident_id == scope_id,
                    AnalysisRun.conversation_id == conversation_id,
                    AnalysisRun.status == "completed",
                )
            )
    else:
        queries = []
        for model, kind, result, outcome in (
            (
                InspectionItem,
                "inspection_item",
                InspectionItem.agent_result,
                InspectionItem.verdict,
            ),
            (
                InspectionCheckResult,
                "inspection_check",
                InspectionCheckResult.result,
                InspectionCheckResult.status,
            ),
        ):
            title = (
                InspectionPlan.name + " · " + model.record_id
                if model is InspectionItem
                else InspectionPlan.name
                + " · "
                + model.object_key
                + " · "
                + func.coalesce(result["name"].as_string(), model.check_id)
            )
            query = (
                projection(
                    model,
                    kind,
                    model.plan_id,
                    model.run_id,
                    title,
                    result,
                    outcome,
                )
                .join(InspectionRun, InspectionRun.id == model.run_id)
                .join(
                    InspectionPlan,
                    InspectionPlan.id == model.plan_id,
                )
                .where(
                    model.plan_id == scope_id,
                    InspectionRun.deleted_at.is_(None),
                    InspectionPlan.deleted_at.is_(None),
                )
            )
            if model is InspectionItem:
                # Unified checks store an object rollup here, not another Agent evaluation.
                query = query.where(
                    model.agent_status == "completed", model.check_status.is_(None)
                )
            else:
                query = query.where(model.kind == "agent", model.execution_status == "completed")
            if run_id:
                query = query.where(model.run_id == run_id)
            queries.append(query)
    if exclude_id:
        queries = [
            query.where(
                MODELS[kind].id != exclude_id
                if business == "diagnosis"
                else MODELS[kind].run_id != exclude_id
            )
            for query, kind in zip(
                queries,
                ["diagnosis", "conversation"]
                if business == "diagnosis"
                else ["inspection_item", "inspection_check"],
                strict=False,
            )
        ]
    return union_all(*queries).subquery()


def search(db, source, query, offset, limit):
    statement = select(*(column for column in source.c if column.key != "search_text"))
    if query.strip():
        pattern = (
            "%" + query.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        )
        statement = statement.where(
            or_(
                source.c.title.ilike(pattern, escape="\\"),
                source.c.search_text.ilike(pattern, escape="\\"),
            )
        )
    total = db.scalar(select(func.count()).select_from(statement.subquery()))
    rows = (
        db.execute(
            statement.order_by(
                source.c.created_at.desc(),
                source.c.kind,
                source.c.id.desc(),
            )
            .offset(offset)
            .limit(limit)
        )
        .mappings()
        .all()
    )
    return rows, total


def traces(db, result_id):
    return db.scalars(
        select(InspectionTrace)
        .where(
            InspectionTrace.check_result_id == result_id,
            InspectionTrace.deleted_at.is_(None),
        )
        .order_by(InspectionTrace.created_at, InspectionTrace.id)
    ).all()


def messages(db, run_id):
    return db.scalars(
        select(AnalysisMessage)
        .where(
            AnalysisMessage.run_id == run_id,
            AnalysisMessage.deleted_at.is_(None),
            AnalysisMessage.status.in_(("accepted", "committed")),
        )
        .order_by(AnalysisMessage.created_at, AnalysisMessage.id)
    ).all()


def draft_lock(db, source):
    db.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:source, 0))"), {"source": source}
    )


def existing_draft(db, source):
    return db.scalar(
        select(KnowledgeDocument)
        .where(
            KnowledgeDocument.source == source,
            KnowledgeDocument.deleted_at.is_(None),
        )
        .order_by(KnowledgeDocument.created_at.desc())
        .limit(1)
    )
