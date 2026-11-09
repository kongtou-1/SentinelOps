"""Storage for private ask-data conversations, runs and durable step events."""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, select

from app.models import AskDataConversation, AskDataEvent, AskDataRun

TERMINAL_STATUSES = frozenset(
    {"completed", "needs_clarification", "failed", "cancelled", "interrupted"}
)


def terminal(status):
    return status in TERMINAL_STATUSES


def conversation(db, identifier, owner_id, *, lock=False):
    statement = (
        select(AskDataConversation).where(
            AskDataConversation.id == identifier,
            AskDataConversation.owner_id == owner_id,
            AskDataConversation.deleted_at.is_(None),
        )
    )
    if lock:
        statement = statement.with_for_update().execution_options(populate_existing=True)
    return db.scalar(statement)


def delete_conversation(db, row):
    active = db.scalars(
        select(AskDataRun)
        .where(
            AskDataRun.conversation_id == row.id,
            AskDataRun.deleted_at.is_(None),
            AskDataRun.status.in_(("queued", "running")),
        )
        .order_by(AskDataRun.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).all()
    now = datetime.now(UTC)
    for run in active:
        run.status, run.error_code = "cancelled", "run_cancelled"
        run.updated_at = now
    row.deleted_at = row.updated_at = now
    return active


def conversations(db, owner_id, connection_id=None, limit=50, offset=0):
    statement = select(AskDataConversation).where(
        AskDataConversation.owner_id == owner_id,
        AskDataConversation.deleted_at.is_(None),
    )
    if connection_id is not None:
        statement = statement.where(AskDataConversation.connection_id == connection_id)
    return db.scalars(
        statement.order_by(AskDataConversation.created_at.desc(), AskDataConversation.id)
        .limit(limit)
        .offset(offset)
    ).all()


def create_conversation(db, connection_id, owner_id, title):
    row = AskDataConversation(
        connection_id=connection_id, owner_id=owner_id, title=title or "新问数"
    )
    db.add(row)
    return row


def runs(db, conversation_id, limit=100):
    return db.scalars(
        select(AskDataRun)
        .where(
            AskDataRun.conversation_id == conversation_id,
            AskDataRun.deleted_at.is_(None),
        )
        .order_by(AskDataRun.created_at, AskDataRun.id)
        .limit(limit)
    ).all()


def run(db, identifier, owner_id):
    return db.scalar(
        select(AskDataRun)
        .join(AskDataConversation, AskDataConversation.id == AskDataRun.conversation_id)
        .where(
            AskDataRun.id == identifier,
            AskDataRun.deleted_at.is_(None),
            AskDataConversation.owner_id == owner_id,
            AskDataConversation.deleted_at.is_(None),
        )
    )


def worker_run(db, identifier, *, lock=False):
    statement = select(AskDataRun).where(
        AskDataRun.id == identifier, AskDataRun.deleted_at.is_(None)
    )
    if lock:
        statement = statement.with_for_update().execution_options(populate_existing=True)
    return db.scalar(statement)


def recent_terminal_runs(db, conversation_id, current_run_id, limit=3):
    rows = db.scalars(
        select(AskDataRun)
        .where(
            AskDataRun.conversation_id == conversation_id,
            AskDataRun.id != current_run_id,
            AskDataRun.deleted_at.is_(None),
            AskDataRun.status.in_(TERMINAL_STATUSES),
        )
        .order_by(AskDataRun.created_at.desc(), AskDataRun.id.desc())
        .limit(limit)
    ).all()
    return list(reversed(rows))


def run_by_key(db, conversation_id, key):
    return db.scalar(
        select(AskDataRun).where(
            AskDataRun.conversation_id == conversation_id,
            AskDataRun.idempotency_key == key,
            AskDataRun.deleted_at.is_(None),
        )
    )


def create_run(db, conversation, model_version_id, question, key):
    row = AskDataRun(
        connection_id=conversation.connection_id,
        conversation_id=conversation.id,
        model_version_id=model_version_id,
        idempotency_key=key,
        status="queued",
        question=question,
        result={},
    )
    db.add(row)
    return row


def claim_run(db):
    row = db.scalar(
        select(AskDataRun)
        .where(AskDataRun.status == "queued", AskDataRun.deleted_at.is_(None))
        .order_by(AskDataRun.created_at, AskDataRun.id)
        .with_for_update(skip_locked=True)
    )
    if row is not None:
        row.status = "running"
    return row


def recover_interrupted_runs(db):
    """Never replay a query after a worker restart; only close its viewing state."""
    rows = db.scalars(
        select(AskDataRun).where(
            AskDataRun.status == "running", AskDataRun.deleted_at.is_(None)
        )
    ).all()
    now = datetime.now(UTC)
    for row in rows:
        row.status, row.error_code = "interrupted", "run_interrupted"
        row.result = {**(row.result or {}), "interrupted_at": now.isoformat()}
    return rows


def events_after(db, run_id, after_seq=0, limit=100):
    return db.scalars(
        select(AskDataEvent)
        .where(
            AskDataEvent.run_id == run_id,
            AskDataEvent.seq > after_seq,
            AskDataEvent.deleted_at.is_(None),
        )
        .order_by(AskDataEvent.seq)
        .limit(limit)
    ).all()


def latest_seq(db, run_id):
    return int(
        db.scalar(
            select(func.coalesce(func.max(AskDataEvent.seq), 0)).where(
                AskDataEvent.run_id == run_id
            )
        )
        or 0
    )


def append_event(db, run_id, kind, data):
    """Serialize worker and API event writers until the caller ends its transaction."""
    worker_run(db, run_id, lock=True)
    sequence = latest_seq(db, run_id) + 1
    row = AskDataEvent(run_id=run_id, seq=sequence, kind=kind, data=data or {})
    db.add(row)
    return row


def context_event(db, run_id):
    row = db.scalar(
        select(AskDataEvent).where(
            AskDataEvent.run_id == run_id,
            AskDataEvent.kind == "context",
            AskDataEvent.deleted_at.is_(None),
        )
    )
    return row.data if row is not None else None


def cancelled(db, run_id):
    row = db.scalar(
        select(AskDataRun.status).where(AskDataRun.id == run_id)
    )
    return row in ("cancelled", "interrupted")


def cancel(db, run_id):
    row = worker_run(db, run_id, lock=True)
    if row is None or terminal(row.status):
        return False
    row.status, row.error_code = "cancelled", "run_cancelled"
    return True


def finish_run(db, run_id, status, result=None, error_code=None):
    row = worker_run(db, run_id, lock=True)
    if row is None:
        return None
    # A user cancellation always wins over a late completion.
    if row.status in ("cancelled", "interrupted"):
        return row
    row.status = status
    if result is not None:
        row.result = result
    if error_code is not None:
        row.error_code = error_code
    row.updated_at = datetime.now(UTC)
    return row


def run_status(db, run_id):
    return db.scalar(select(AskDataRun.status).where(AskDataRun.id == run_id))


def active_version(db, connection_id):
    from app.models import AskDataConfiguration

    return db.scalar(
        select(AskDataConfiguration.active_model_version_id).where(
            AskDataConfiguration.connection_id == connection_id,
            AskDataConfiguration.deleted_at.is_(None),
            AskDataConfiguration.active_model_version_id.is_not(None),
        )
    )


def model_version_source(db, version_id) -> UUID | None:
    from app.models import AskDataModelVersion

    return db.scalar(
        select(AskDataModelVersion.source_snapshot_id).where(
            AskDataModelVersion.id == version_id
        )
    )
