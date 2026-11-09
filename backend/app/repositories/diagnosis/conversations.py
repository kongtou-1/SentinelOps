"""Database access; transaction ownership stays with the caller."""

from uuid import UUID

from sqlalchemy import select

from app.models import (
    AnalysisConversation,
    AnalysisMessage,
    AnalysisRun,
    KnowledgeChunk,
    KnowledgeDocument,
    User,
)


def access_query(db, identifier):
    row = db.get(AnalysisConversation, identifier, with_for_update=True, populate_existing=True)
    return row


def snapshot_query(db, row):
    owner = db.get(User, row.owner_id)
    run = db.scalar(
        select(AnalysisRun)
        .where(AnalysisRun.conversation_id == row.id, AnalysisRun.deleted_at.is_(None))
        .order_by(AnalysisRun.created_at.desc(), AnalysisRun.id.desc())
        .limit(1)
    )
    return owner, run


def event_query(db, item):
    db.add(item)


def send_query(body, db, identifier):
    previous = db.scalar(
        select(AnalysisMessage).where(
            AnalysisMessage.conversation_id == identifier,
            AnalysisMessage.client_message_id == body.client_message_id,
            AnalysisMessage.deleted_at.is_(None),
        )
    )
    return previous


def send_query_2(ACTIVE, db, identifier):
    run = db.scalar(
        select(AnalysisRun).where(
            AnalysisRun.conversation_id == identifier,
            AnalysisRun.status.in_(ACTIVE),
            AnalysisRun.deleted_at.is_(None),
        )
    )
    return run


def send_query_3(db, run):
    db.add(run)
    db.flush()


def send_query_4(db, identifier):
    return db.scalars(
        select(AnalysisMessage).where(
            AnalysisMessage.conversation_id == identifier,
            AnalysisMessage.role == "user",
            AnalysisMessage.status.in_(("next_round", "unknown")),
            AnalysisMessage.deleted_at.is_(None),
        )
    )


def send_query_5(db, message):
    db.add(message)
    db.flush()


def send_query_6(db, message):
    db.refresh(message)


def cancel_query(db, run):
    db.refresh(run)


def cancel_query_2(db, run):
    db.refresh(run)


def leave_pending_query(db, run):
    return db.scalars(
        select(AnalysisMessage).where(
            AnalysisMessage.run_id == run.id,
            AnalysisMessage.role == "user",
            AnalysisMessage.status.in_(("saved", "dispatched", "waiting")),
        )
    )


def leave_pending_query_2(db, run):
    return db.scalars(
        select(AnalysisMessage).where(
            AnalysisMessage.run_id == run.id,
            AnalysisMessage.role == "assistant",
            AnalysisMessage.status == "streaming",
        )
    )


def valid_knowledge_query(db, item):
    chunk = db.get(KnowledgeChunk, UUID(item["id"]))
    document = db.get(KnowledgeDocument, chunk.document_id) if chunk else None
    return chunk, document


def adopt_query(db, run):
    db.refresh(run)


def adoption_runs(db, run):
    return db.scalars(
        select(AnalysisRun)
        .where(
            AnalysisRun.conversation_id == run.conversation_id,
            AnalysisRun.created_at <= run.created_at,
            AnalysisRun.deleted_at.is_(None),
        )
        .order_by(AnalysisRun.created_at, AnalysisRun.id)
    ).all()


def adopt_query_2(analysis, db):
    db.add(analysis)
    db.flush()


def adopt_query_3(analysis, db):
    db.refresh(analysis)
