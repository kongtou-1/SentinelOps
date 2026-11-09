"""Database access; transaction ownership stays with the caller."""

from sqlalchemy import select, text

from app.models import AgentAnalysis, KnowledgeDocument


def index_once_query(db):
    document = db.scalar(
        select(KnowledgeDocument)
        .where(KnowledgeDocument.index_status == "queued", KnowledgeDocument.deleted_at.is_(None))
        .order_by(KnowledgeDocument.created_at)
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    return document


def index_once_query_2(db, identifier):
    document = db.get(KnowledgeDocument, identifier, with_for_update=True)
    return document


def analyze_once_query(db):
    row = db.scalar(
        select(AgentAnalysis)
        .where(AgentAnalysis.status == "queued", AgentAnalysis.deleted_at.is_(None))
        .order_by(AgentAnalysis.created_at)
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    return row


def main_query(owner):
    return owner.scalar(text("SELECT pg_try_advisory_lock(80008)"))


def main_query_2(owner):
    owner.execute(text("SELECT 1"))
