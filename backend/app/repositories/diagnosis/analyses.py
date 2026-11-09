"""Database access; transaction ownership stays with the caller."""

from uuid import UUID

from sqlalchemy import select

from app.models import (
    AgentAnalysis,
    Container,
    Incident,
    IncidentTimeline,
    KnowledgeDocument,
    KnowledgeSkill,
    LLMEndpoint,
    PredictionTask,
    Role,
    Server,
    User,
)


def scope_query(db, item):
    task = db.get(PredictionTask, item.prediction_task_id)
    return task


def scope_query_2(db, task):
    container = db.get(Container, task.container_id)
    return container


def scope_query_3(db, server_ids):
    servers = db.scalars(
        select(Server).where(Server.id.in_(server_ids), Server.deleted_at.is_(None))
    ).all()
    return servers


def scope_query_4(db, endpoint_ids, server_ids):
    containers = db.scalars(
        select(Container).where(Container.server_id.in_(server_ids), Container.deleted_at.is_(None))
    ).all()
    endpoints = db.scalars(
        select(LLMEndpoint).where(
            LLMEndpoint.id.in_(endpoint_ids), LLMEndpoint.deleted_at.is_(None)
        )
    ).all()
    return containers, endpoints


def fingerprint_query(db):
    return db.scalars(select(KnowledgeDocument).where(KnowledgeDocument.deleted_at.is_(None)))


def fingerprint_query_2(db):
    return db.scalars(select(KnowledgeSkill).where(KnowledgeSkill.deleted_at.is_(None)))


def fingerprint_query_3(db, incident):
    return db.scalars(
        select(IncidentTimeline).where(
            IncidentTimeline.incident_id == incident.id,
            IncidentTimeline.event_type == "comment_added",
        )
    )


def latest_query(db, incident_id):
    return db.scalar(
        select(AgentAnalysis)
        .where(AgentAnalysis.incident_id == incident_id, AgentAnalysis.deleted_at.is_(None))
        .order_by(AgentAnalysis.created_at.desc(), AgentAnalysis.id.desc())
        .limit(1)
    )


def enqueue_query(db, incident_id):
    active = db.scalar(
        select(AgentAnalysis).where(
            AgentAnalysis.incident_id == incident_id,
            AgentAnalysis.status.in_(("queued", "running")),
            AgentAnalysis.deleted_at.is_(None),
        )
    )
    return active


def enqueue_query_2(db, row):
    db.add(row)
    db.flush()


def enqueue_query_3(db, row):
    db.refresh(row)


def requester_valid_query(db, row):
    from app.repositories.access import capability_condition

    return db.scalar(
        select(User.id)
        .join(Role)
        .where(
            User.id == row.requested_by,
            User.deleted_at.is_(None),
            User.is_active.is_(True),
            Role.deleted_at.is_(None),
            capability_condition("analyses:run"),
        )
    )


def complete_query(db, run_id):
    row = db.get(AgentAnalysis, UUID(str(run_id)), with_for_update=True)
    incident = db.get(Incident, row.incident_id)
    return incident, row


def interrupt_runs_query(db):
    return db.scalars(select(AgentAnalysis).where(AgentAnalysis.status == "running"))


def interrupt_runs_query_2(db, row):
    incident = db.get(Incident, row.incident_id)
    return incident


def interrupt_runs_query_3(db):
    return db.scalars(select(KnowledgeDocument).where(KnowledgeDocument.index_status == "running"))
