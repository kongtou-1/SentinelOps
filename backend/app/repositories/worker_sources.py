"""Database access; transaction ownership stays with the caller."""

from datetime import UTC, datetime

from sqlalchemy import select, text, update

from app.models import Application, SourceAuditLog, SourceSnapshot


def sync_once_query(db):
    snapshot = db.scalar(
        select(SourceSnapshot)
        .where(
            SourceSnapshot.status == "queued",
            SourceSnapshot.deleted_at.is_(None),
        )
        .order_by(SourceSnapshot.created_at)
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    return snapshot


def sync_once_query_2(db, snapshot):
    app = db.get(Application, snapshot.application_id)
    return app


def sync_once_query_3(db, identifier):
    snapshot = db.get(SourceSnapshot, identifier, with_for_update=True)
    return snapshot


def sync_once_query_4(db, identifier, snapshot):
    db.add(
        SourceAuditLog(
            application_id=snapshot.application_id,
            user_id=snapshot.requested_by,
            action="source_sync_finished",
            detail={
                "snapshot_id": str(identifier),
                "status": snapshot.status,
                "error_code": snapshot.error_code,
            },
        )
    )


def main_query(owner):
    return owner.scalar(text("SELECT pg_try_advisory_lock(80013)"))


def main_query_2(db):
    db.execute(
        update(SourceSnapshot)
        .where(SourceSnapshot.status == "running")
        .values(
            status="interrupted",
            error_code="worker_restarted",
            finished_at=datetime.now(UTC),
        )
    )


def main_query_3(owner):
    owner.execute(text("SELECT 1"))
