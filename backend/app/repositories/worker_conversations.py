"""Database access; transaction ownership stays with the caller."""

from uuid import UUID

from sqlalchemy import select

from app.models import AnalysisConversation, AnalysisMessage, AnalysisRun, Incident


def record_query(db, run_id):
    run = db.get(AnalysisRun, run_id)
    conversation = db.get(AnalysisConversation, run.conversation_id, with_for_update=True)
    db.refresh(run)
    return conversation, run


def record_query_2(db, identifier):
    message = db.get(AnalysisMessage, UUID(identifier))
    return message


def record_query_3(db, key, run):
    message = db.scalar(
        select(AnalysisMessage).where(
            AnalysisMessage.run_id == run.id, AnalysisMessage.stream_key == key
        )
    )
    return message


def record_query_4(db, message):
    db.add(message)


def record_query_5(db, run):
    candidates = db.scalars(
        select(AnalysisMessage)
        .where(
            AnalysisMessage.run_id == run.id,
            AnalysisMessage.role == "assistant",
            AnalysisMessage.status == "streaming",
        )
        .order_by(AnalysisMessage.created_at.desc())
    ).all()
    return candidates


def record_query_6(db, message):
    db.add(message)


def stop_reason_query(db, run_id):
    run = db.get(AnalysisRun, run_id)
    incident = db.get(Incident, run.incident_id)
    return incident, run


def pending_query(db, run_id):
    run = db.get(AnalysisRun, run_id)
    conversation = db.get(AnalysisConversation, run.conversation_id, with_for_update=True)
    db.refresh(run)
    return conversation, run


def pending_query_2(db, run_id):
    rows = db.scalars(
        select(AnalysisMessage)
        .where(
            AnalysisMessage.run_id == run_id,
            AnalysisMessage.role == "user",
            AnalysisMessage.status == "saved",
            AnalysisMessage.deleted_at.is_(None),
        )
        .order_by(AnalysisMessage.created_at, AnalysisMessage.id)
    ).all()
    return rows


def finish_query(db, run_id):
    run = db.get(AnalysisRun, run_id)
    conversation = db.get(AnalysisConversation, run.conversation_id, with_for_update=True)
    db.refresh(run)
    return conversation, run


def conversation_once_query(db):
    run = db.scalar(
        select(AnalysisRun)
        .where(AnalysisRun.status == "queued", AnalysisRun.deleted_at.is_(None))
        .order_by(AnalysisRun.created_at)
        .limit(1)
    )
    return run


def conversation_once_query_2(db, run):
    conversation = db.get(AnalysisConversation, run.conversation_id, with_for_update=True)
    db.refresh(run)
    return conversation


def conversation_once_query_3(conversation, db, run):
    previous = db.scalars(
        select(AnalysisRun)
        .where(
            AnalysisRun.conversation_id == conversation.id,
            AnalysisRun.id != run.id,
            AnalysisRun.deleted_at.is_(None),
        )
        .order_by(AnalysisRun.created_at.desc(), AnalysisRun.id.desc())
        .limit(8)
    ).all()
    previous.reverse()
    history = db.scalars(
        select(AnalysisMessage)
        .where(
            AnalysisMessage.conversation_id == conversation.id,
            AnalysisMessage.run_id != run.id,
            AnalysisMessage.status.in_(("accepted", "committed")),
            AnalysisMessage.deleted_at.is_(None),
        )
        .order_by(AnalysisMessage.created_at.desc(), AnalysisMessage.id.desc())
        .limit(12)
    ).all()
    history.reverse()
    return history, previous


def interrupt_conversations_query(db):
    return db.scalars(
        select(AnalysisRun).where(
            AnalysisRun.status.in_(("running", "cancelling")), AnalysisRun.deleted_at.is_(None)
        )
    )


def interrupt_conversations_query_2(db, run):
    conversation = db.get(AnalysisConversation, run.conversation_id, with_for_update=True)
    return conversation
