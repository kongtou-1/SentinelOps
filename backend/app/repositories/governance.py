from datetime import timedelta

from sqlalchemy import String, cast, exists, func, or_, select, text

from app.models import (
    AgentAnalysis,
    AlertEvent,
    AnalysisConversation,
    AnalysisEvent,
    AnalysisMessage,
    AnalysisRun,
    ApplicationRelease,
    Approval,
    ArchiveRecord,
    EvaluationCase,
    ImprovementTask,
    IncidentTimeline,
    PostmortemDelivery,
    PostmortemReport,
    PostmortemRevision,
    PredictionOutcome,
    PredictionResult,
    RepairHandoff,
    RetentionPlan,
    SkillExecution,
    SourceSnapshot,
)

MODELS = {
    "conversation": AnalysisConversation,
    "source_snapshot": SourceSnapshot,
    "prediction": PredictionResult,
}
REFERENCES = [
    (
        AgentAnalysis,
        ["source_context", "database_context", "evidence", "knowledge", "result", "tool_calls"],
    ),
    (
        AnalysisRun,
        ["source_context", "database_context", "evidence", "knowledge", "candidate", "tool_calls"],
    ),
    (AlertEvent, ["labels", "annotations", "rule_snapshot"]),
    (PostmortemReport, ["snapshot"]),
    (PostmortemRevision, ["content"]),
    (RepairHandoff, ["packet", "results", "verifications"]),
    (Approval, ["binding"]),
    (SkillExecution, ["precheck", "output", "verification"]),
    (EvaluationCase, ["snapshot"]),
    (IncidentTimeline, ["content"]),
    (ImprovementTask, ["submission", "item"]),
]


def lock(db):
    db.execute(text("SELECT pg_advisory_xact_lock(100031)"))


def candidates(db, kind, cutoff, limit):
    model = MODELS[kind]
    query = select(model).where(
        model.updated_at <= cutoff,
        ~exists(
            select(ArchiveRecord.id).where(
                ArchiveRecord.resource_kind == kind,
                ArchiveRecord.resource_id == model.id,
                ArchiveRecord.status.in_(("releasing", "complete")),
            )
        ),
    )
    return db.scalars(query.order_by(model.updated_at, model.id).limit(limit)).all()


def reference_reasons(db, kind, row, now):
    reasons = []
    identifiers = [str(row.id)]
    if kind == "conversation":
        runs = db.scalars(select(AnalysisRun).where(AnalysisRun.conversation_id == row.id)).all()
        identifiers.extend(str(run.id) for run in runs)
        if any(run.status in ("queued", "running", "cancelling") for run in runs):
            reasons.append("active_task")
        if any(run.adopted_analysis_id for run in runs):
            reasons.append("adopted_analysis")
        if any(run.updated_at > now - timedelta(days=90) for run in runs) or db.scalar(
            select(AnalysisMessage.id)
            .where(
                AnalysisMessage.conversation_id == row.id,
                AnalysisMessage.updated_at > now - timedelta(days=90),
            )
            .limit(1)
        ):
            reasons.append("recent_activity")
        if any(
            db.scalar(
                select(AgentAnalysis.id)
                .where(AgentAnalysis.session_id.contains(identifier))
                .limit(1)
            )
            for identifier in identifiers
        ):
            reasons.append("official_analysis")
    elif kind == "source_snapshot":
        if row.status in ("queued", "running"):
            reasons.append("active_task")
        if db.scalar(
            select(ApplicationRelease.id).where(ApplicationRelease.snapshot_id == row.id).limit(1)
        ):
            reasons.append("release_reference")
    else:
        if row.status in ("queued", "running"):
            reasons.append("active_task")
        if db.scalar(
            select(PredictionResult.id)
            .where(
                PredictionResult.task_id == row.task_id,
                PredictionResult.status.in_(("queued", "running")),
                PredictionResult.deleted_at.is_(None),
            )
            .limit(1)
        ):
            reasons.append("active_task")
        if db.scalar(
            select(PredictionOutcome.id)
            .where(
                PredictionOutcome.result_id == row.id,
                cast(PredictionOutcome.incident_links, String) != "[]",
            )
            .limit(1)
        ):
            reasons.append("confirmed_incident")
    for model, fields in REFERENCES:
        condition = or_(
            *[
                cast(getattr(model, field), String).contains(identifier, autoescape=True)
                for field in fields
                for identifier in identifiers
            ]
        )
        if db.scalar(select(model.id).where(condition).limit(1)):
            reasons.append("evidence_reference:" + model.__tablename__)
    return sorted(set(reasons))


def archive_for(db, kind, identifier):
    return db.scalar(
        select(ArchiveRecord)
        .where(ArchiveRecord.resource_kind == kind, ArchiveRecord.resource_id == identifier)
        .with_for_update()
    )


def claim_plan(db):
    return db.scalar(
        select(RetentionPlan)
        .where(RetentionPlan.status.in_(("queued", "running")), RetentionPlan.deleted_at.is_(None))
        .order_by(RetentionPlan.created_at)
        .limit(1)
        .with_for_update(skip_locked=True)
    )


def plans():
    return (
        select(RetentionPlan)
        .where(RetentionPlan.deleted_at.is_(None))
        .order_by(RetentionPlan.created_at.desc())
    )


def archives():
    return (
        select(ArchiveRecord)
        .where(ArchiveRecord.deleted_at.is_(None))
        .order_by(ArchiveRecord.created_at.desc())
    )


def accessible_archives(user, resource_id=None):
    query = archives()
    if user.role.code != "admin":
        allowed = ArchiveRecord.resource_kind.in_(("source_snapshot", "prediction"))
        allowed = or_(
            allowed,
            (ArchiveRecord.resource_kind == "conversation") & (ArchiveRecord.owner_id == user.id),
        )
        query = query.where(allowed)
    if resource_id:
        query = query.where(ArchiveRecord.resource_id == resource_id)
    return query


def conversation_rows(db, identifier):
    return {
        name: db.scalars(select(model).where(model.conversation_id == identifier)).all()
        for name, model in (
            ("runs", AnalysisRun),
            ("messages", AnalysisMessage),
            ("events", AnalysisEvent),
        )
    }


def bytes_for(db, kind, row):
    if kind == "source_snapshot":
        return sum(int(item.get("bytes", 0)) for item in row.manifest)
    if kind == "prediction":
        return (
            db.scalar(
                select(
                    func.pg_column_size(PredictionResult.horizons)
                    + func.pg_column_size(PredictionResult.backtest)
                    + func.pg_column_size(PredictionResult.anomalies)
                    + func.pg_column_size(PredictionResult.detector_state)
                ).where(PredictionResult.id == row.id)
            )
            or 0
        )
    return sum(
        db.scalar(
            select(
                func.coalesce(func.sum(func.pg_column_size(model.__table__.table_valued())), 0)
            ).where(model.conversation_id == row.id)
        )
        or 0
        for model in (AnalysisRun, AnalysisMessage, AnalysisEvent)
    )


def release_transaction(db):
    db.execute(text("SELECT set_config('sentinelops.retention_writer','on',true)"))


def source_running(db):
    return (
        db.scalar(
            select(SourceSnapshot.id)
            .where(SourceSnapshot.status == "running", SourceSnapshot.deleted_at.is_(None))
            .limit(1)
        )
        is not None
    )


def storage_tables(db):
    return [
        dict(row._mapping)
        for row in db.execute(
            text(
                "SELECT relname AS name,pg_total_relation_size(relid) AS bytes "
                "FROM pg_catalog.pg_statio_user_tables "
                "ORDER BY pg_total_relation_size(relid) DESC"
            )
        )
    ]


def attachment_bytes(db):
    return db.scalar(
        select(func.coalesce(func.sum(func.octet_length(PostmortemDelivery.attachment)), 0))
    )
