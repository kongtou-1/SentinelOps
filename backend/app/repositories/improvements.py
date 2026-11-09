from sqlalchemy import select, text

from app.models import (
    ApplicationDeployment,
    ApplicationRelease,
    ImprovementTask,
    KnowledgeDocument,
    PostmortemRevision,
)


def tasks(report_id=None, status=None):
    query = select(ImprovementTask).where(ImprovementTask.deleted_at.is_(None))
    if report_id:
        query = query.where(ImprovementTask.report_id == report_id)
    if status:
        query = query.where(ImprovementTask.status == status)
    return query.order_by(ImprovementTask.created_at.desc(), ImprovementTask.id)


def import_lock(db, report_id):
    db.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))"),
        {"key": "improvement:" + str(report_id)},
    )


def existing(db, report_id, revision, index):
    return db.scalar(
        select(ImprovementTask).where(
            ImprovementTask.report_id == report_id,
            ImprovementTask.report_revision == revision,
            ImprovementTask.item_index == index,
        )
    )


def revision(db, report_id, number):
    return db.scalar(
        select(PostmortemRevision).where(
            PostmortemRevision.report_id == report_id,
            PostmortemRevision.revision == number,
            PostmortemRevision.deleted_at.is_(None),
        )
    )


def overdue(db, now):
    return db.scalars(
        select(ImprovementTask).where(
            ImprovementTask.deleted_at.is_(None),
            ImprovementTask.due_at <= now,
            ImprovementTask.status.in_(("unassigned", "pending", "in_progress", "awaiting_review")),
        )
    ).all()


def current_versions(db, application_ids):
    from datetime import UTC, datetime

    result = {str(identifier): [] for identifier in application_ids}
    rows = db.execute(
        select(ApplicationDeployment.application_id, ApplicationRelease.version)
        .join(ApplicationDeployment, ApplicationDeployment.release_id == ApplicationRelease.id)
        .where(
            ApplicationDeployment.application_id.in_(application_ids),
            ApplicationDeployment.ended_at.is_(None),
            ApplicationDeployment.started_at <= datetime.now(UTC),
            ApplicationDeployment.deleted_at.is_(None),
            ApplicationRelease.deleted_at.is_(None),
        )
    ).all()
    for identifier, version in rows:
        result[str(identifier)].append(version)
    return result


def report_draft(db, source):
    return db.scalar(
        select(KnowledgeDocument).where(
            KnowledgeDocument.source == source, KnowledgeDocument.deleted_at.is_(None)
        )
    )
