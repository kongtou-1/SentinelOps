from datetime import UTC, datetime

from fastapi import HTTPException

import app.repositories.knowledge.management as _repository
from app.models import KnowledgeDocument, KnowledgeSkill
from app.services.knowledge.index import search, withdraw
from app.services.pagination import page
from app.services.records import active_record, save
from app.utils.redaction import redact


def documents(response, db, user, offset=0, limit=50):
    return page(
        db,
        _repository.documents_query(),
        response,
        offset,
        limit,
    )


def retrieve(db, user, q, tag=None, limit=5):
    return search(db, redact(q), tag, limit)


def create_document(body, db, user):
    return save(db, KnowledgeDocument(**redact(body.model_dump())))


def document(document_id, db, user):
    return active_record(db, KnowledgeDocument, document_id)


def replace_document(document_id, body, db, user):
    row = active_record(db, KnowledgeDocument, document_id)
    if row.source.startswith("agent-result:") and body.source != row.source:
        raise HTTPException(422, "Agent result provenance cannot be changed")
    publishing = row.source.startswith("agent-result:") and body.enabled and not row.enabled
    if publishing:
        from uuid import UUID

        from app.repositories.notifications import audit
        from app.services.knowledge.agent_results import source_record

        _, kind, identifier = row.source.split(":", 2)
        source_record(db, user, kind, UUID(identifier))
        audit(
            db,
            user,
            "agent_result_knowledge_published",
            "knowledge_document",
            row.id,
            {"source": row.source},
        )
    _repository.replace_document_query(db, row)
    for name, value in redact(body.model_dump()).items():
        setattr(row, name, value)
    row.version += 1
    row.index_status, row.error_code = "queued", None
    withdraw(db, row.id)
    return save(db, row)


def reindex(document_id, db, user):
    row = active_record(db, KnowledgeDocument, document_id)
    _repository.reindex_query(db, row)
    if row.index_status in ("queued", "running"):
        raise HTTPException(409, "Document indexing is already queued or running")
    row.version += 1
    row.index_status, row.error_code = "queued", None
    withdraw(db, row.id)
    return save(db, row)


def delete_document(document_id, db, user):
    row = active_record(db, KnowledgeDocument, document_id)
    _repository.delete_document_query(db, row)
    row.deleted_at = datetime.now(UTC)
    withdraw(db, row.id)
    db.commit()


def skills(response, db, user, offset=0, limit=50):
    return page(
        db,
        _repository.skills_query(),
        response,
        offset,
        limit,
    )


def create_skill(body, db, user):
    return save(db, KnowledgeSkill(**redact(body.model_dump())))


def skill(skill_id, db, user):
    return active_record(db, KnowledgeSkill, skill_id)


def replace_skill(skill_id, body, db, user):
    row = active_record(db, KnowledgeSkill, skill_id)
    _repository.replace_skill_query(db, row)
    for name, value in redact(body.model_dump()).items():
        if name == "resources" and name not in body.model_fields_set:
            continue
        setattr(row, name, value)
    row.version += 1
    return save(db, row)


def delete_skill(skill_id, db, user):
    row = active_record(db, KnowledgeSkill, skill_id)
    row.deleted_at = datetime.now(UTC)
    db.commit()
