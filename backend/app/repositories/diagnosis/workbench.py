"""Database access; transaction ownership stays with the caller."""

from sqlalchemy import select

from app.models import (
    AnalysisConversation,
    AnalysisEvent,
    AnalysisMessage,
    AnalysisRun,
)


def create_query(db, row):
    db.add(row)


def create_query_2(db, row):
    db.refresh(row)


def listing_query(incident_id):
    statement = select(AnalysisConversation).where(
        AnalysisConversation.incident_id == incident_id, AnalysisConversation.deleted_at.is_(None)
    )
    return statement


def listing_query_2(statement):
    statement = statement.where(False)
    return statement


def listing_query_3(statement, user):
    statement = statement.where(AnalysisConversation.owner_id == user.id)
    return statement


def listing_query_4(owner_id, statement):
    statement = statement.where(AnalysisConversation.owner_id == owner_id)
    return statement


def listing_query_5(statement):
    return statement.order_by(
        AnalysisConversation.updated_at.desc(), AnalysisConversation.id.desc()
    )


def messages_query(identifier):
    return (
        select(AnalysisMessage)
        .where(AnalysisMessage.conversation_id == identifier, AnalysisMessage.deleted_at.is_(None))
        .order_by(AnalysisMessage.created_at, AnalysisMessage.id)
    )


def runs_query(identifier):
    return (
        select(AnalysisRun)
        .where(AnalysisRun.conversation_id == identifier, AnalysisRun.deleted_at.is_(None))
        .order_by(AnalysisRun.created_at.desc(), AnalysisRun.id.desc())
    )


def events_query(after_seq, db, identifier, limit):
    return db.scalars(
        select(AnalysisEvent)
        .where(
            AnalysisEvent.conversation_id == identifier,
            AnalysisEvent.seq > after_seq,
            AnalysisEvent.deleted_at.is_(None),
        )
        .order_by(AnalysisEvent.seq)
        .limit(limit)
    )


def stream_generate_query(cursor, identifier, session):
    batch = session.scalars(
        select(AnalysisEvent)
        .where(
            AnalysisEvent.conversation_id == identifier,
            AnalysisEvent.seq > cursor,
            AnalysisEvent.deleted_at.is_(None),
        )
        .order_by(AnalysisEvent.seq)
        .limit(100)
    ).all()
    return batch
