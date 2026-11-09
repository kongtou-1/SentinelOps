"""Database access; transaction ownership stays with the caller."""

from datetime import UTC, datetime

from sqlalchemy import cast, select, update
from sqlalchemy.dialects.postgresql import JSONB

from app.models import KnowledgeChunk, KnowledgeDocument
from app.repositories.knowledge.scope import predicates


def withdraw_query(db, document_id):
    db.execute(
        update(KnowledgeChunk)
        .where(KnowledgeChunk.document_id == document_id, KnowledgeChunk.deleted_at.is_(None))
        .values(deleted_at=datetime.now(UTC))
    )


def build_index_query(db, document_id):
    document = db.get(KnowledgeDocument, document_id, with_for_update=True)
    return document


def build_index_query_2(content, db, document_id, position, vector, version):
    db.add(
        KnowledgeChunk(
            document_id=document_id,
            version=version,
            position=position,
            content=content,
            embedding=vector.tolist(),
        )
    )


def search_query(distance, application_ids=None, versions=None):
    statement = (
        select(KnowledgeChunk, KnowledgeDocument, distance.label("distance"))
        .join(KnowledgeDocument)
        .where(
            KnowledgeChunk.deleted_at.is_(None),
            KnowledgeDocument.deleted_at.is_(None),
            KnowledgeDocument.enabled.is_(True),
            KnowledgeDocument.index_status == "ready",
            KnowledgeChunk.version == KnowledgeDocument.version,
            *predicates(KnowledgeDocument, application_ids, versions),
        )
    )
    return statement


def search_query_2(statement, tag):
    statement = statement.where(cast(KnowledgeDocument.tags, JSONB).contains([tag]))
    return statement


def search_query_3(db, distance, limit, statement):
    rows = db.execute(statement.order_by(distance, KnowledgeChunk.id).limit(limit)).all()
    return rows
