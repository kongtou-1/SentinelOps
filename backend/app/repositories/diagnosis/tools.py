"""Database access; transaction ownership stays with the caller."""

from sqlalchemy import select

from app.models import (
    Container,
    Incident,
    IncidentTimeline,
    KnowledgeSkill,
    LLMEndpoint,
    PredictionResult,
    PredictionTask,
    Server,
    SSHCredential,
)
from app.repositories.knowledge.scope import predicates


def DiagnosticTools__call_query(db, self):
    row = db.get(self.row_model, self.analysis_id)
    incident = db.get(Incident, row.incident_id)
    return incident, row


def DiagnosticTools__call_query_2(db, self):
    row = db.get(self.row_model, self.analysis_id, with_for_update=True)
    return row


def DiagnosticTools_dispatch_query(db, incident):
    notes = db.scalars(
        select(IncidentTimeline)
        .where(
            IncidentTimeline.incident_id == incident.id,
            IncidentTimeline.event_type == "comment_added",
        )
        .order_by(IncidentTimeline.created_at.desc())
        .limit(20)
    ).all()
    return notes


def DiagnosticTools_dispatch_query_2(body, db):
    asset = db.get(Container, body.asset_id)
    return asset


def DiagnosticTools_dispatch_query_3(body, db):
    asset = db.get(Server, body.asset_id)
    return asset


def DiagnosticTools_dispatch_query_4(body, db):
    return db.get(LLMEndpoint, body.asset_id)


def DiagnosticTools_dispatch_query_5(body, db):
    container = db.get(Container, body.asset_id)
    return container


def DiagnosticTools_dispatch_query_6(container, db):
    server = db.scalar(
        select(Server)
        .where(Server.id == container.server_id, Server.deleted_at.is_(None))
        .execution_options(access_internal=True)
    )
    credential = db.get(SSHCredential, server.credential_id) if server.credential_id else None
    return credential, server


def DiagnosticTools_dispatch_query_7(db):
    tasks = db.scalars(select(PredictionTask).where(PredictionTask.deleted_at.is_(None))).all()
    return tasks


def DiagnosticTools_dispatch_query_8(db, task):
    last = db.scalar(
        select(PredictionResult)
        .where(
            PredictionResult.task_id == task.id,
            PredictionResult.status == "completed",
            PredictionResult.deleted_at.is_(None),
        )
        .order_by(PredictionResult.created_at.desc())
        .limit(1)
    )
    return last


def DiagnosticTools_dispatch_query_9(db, application_ids=None, versions=None):
    return db.scalars(
        select(KnowledgeSkill)
        .where(
            KnowledgeSkill.enabled.is_(True),
            KnowledgeSkill.deleted_at.is_(None),
            *predicates(KnowledgeSkill, application_ids, versions),
        )
        .order_by(KnowledgeSkill.code)
    )


def DiagnosticTools_dispatch_query_10(body, db, application_ids=None, versions=None):
    skill = db.scalar(
        select(KnowledgeSkill).where(
            KnowledgeSkill.id == body.skill_id,
            KnowledgeSkill.enabled.is_(True),
            KnowledgeSkill.deleted_at.is_(None),
            *predicates(KnowledgeSkill, application_ids, versions),
        )
    )
    return skill
