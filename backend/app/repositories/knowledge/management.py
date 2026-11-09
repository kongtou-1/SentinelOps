"""Database access; transaction ownership stays with the caller."""

from sqlalchemy import select

from app.models import KnowledgeDocument, KnowledgeSkill


def documents_query():
    return (
        select(KnowledgeDocument)
        .where(KnowledgeDocument.deleted_at.is_(None))
        .order_by(KnowledgeDocument.created_at.desc(), KnowledgeDocument.id)
    )


def replace_document_query(db, row):
    db.refresh(row, with_for_update=True)


def reindex_query(db, row):
    db.refresh(row, with_for_update=True)


def delete_document_query(db, row):
    db.refresh(row, with_for_update=True)


def skills_query():
    return (
        select(KnowledgeSkill)
        .where(KnowledgeSkill.deleted_at.is_(None))
        .order_by(KnowledgeSkill.code)
    )


def replace_skill_query(db, row):
    db.refresh(row, with_for_update=True)
