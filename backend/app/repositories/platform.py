from datetime import timedelta

from sqlalchemy import func, select, text

from app.models import (
    AgentAnalysis,
    AnalysisRun,
    BackupRecord,
    EvaluationRun,
    KnowledgeDocument,
    Notification,
    PostmortemDelivery,
    PostmortemReport,
    PredictionResult,
    RetentionPlan,
    SkillExecution,
    SourceSnapshot,
    WorkerHeartbeat,
)

QUEUES = [
    ("cleanup", RetentionPlan, "status"),
    ("diagnosis", AgentAnalysis, "status"),
    ("conversations", AnalysisRun, "status"),
    ("evaluation", EvaluationRun, "status"),
    ("prediction", PredictionResult, "status"),
    ("source", SourceSnapshot, "status"),
    ("report", PostmortemReport, "status"),
    ("report_mail", PostmortemDelivery, "status"),
    ("notification", Notification, "mail_status"),
    ("execution", SkillExecution, "status"),
    ("knowledge", KnowledgeDocument, "index_status"),
]


def heartbeats(db):
    return db.scalars(select(WorkerHeartbeat).where(WorkerHeartbeat.deleted_at.is_(None))).all()


def queue_status(db, now):
    result = []
    for name, model, field in QUEUES:
        state = getattr(model, field)
        queued, oldest = db.execute(
            select(func.count(), func.min(model.created_at)).where(
                model.deleted_at.is_(None), state == "queued"
            )
        ).one()
        failed = db.scalar(
            select(func.count())
            .select_from(model)
            .where(
                model.deleted_at.is_(None),
                state.in_(("failed", "interrupted", "unknown")),
                model.updated_at >= now - timedelta(days=1),
            )
        )
        running = db.scalar(
            select(func.count())
            .select_from(model)
            .where(
                model.deleted_at.is_(None),
                state.in_(
                    ("running", "sending", "prechecking", "executing", "verifying", "cancelling")
                ),
            )
        )
        result.append(
            {
                "name": name,
                "queued": queued,
                "running": running,
                "failed_24h": failed,
                "oldest_wait_seconds": max(0, (now - oldest).total_seconds()) if oldest else None,
            }
        )
    return result


def database_bytes(db):
    return db.scalar(text("SELECT pg_database_size(current_database())"))


def backups(kind=None):
    query = select(BackupRecord).where(BackupRecord.deleted_at.is_(None))
    if kind:
        query = query.where(BackupRecord.kind == kind)
    return query.order_by(BackupRecord.completed_at.desc(), BackupRecord.id)


def latest_backup(db):
    return db.scalar(backups("backup").where(BackupRecord.verification == "verified").limit(1))


def latest_restore(db):
    return db.scalar(backups("restore").limit(1))


def idempotent(db, key):
    db.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))"),
        {"key": "backup:" + str(key)},
    )
    return db.scalar(select(BackupRecord).where(BackupRecord.idempotency_key == key))
