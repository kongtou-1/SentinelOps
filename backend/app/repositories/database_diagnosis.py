from uuid import UUID

from sqlalchemy import select

from app.models import (
    Application,
    ApplicationDeployment,
    Container,
    LLMServiceBinding,
    PredictionTask,
    Server,
    ServiceDatabase,
    ServiceMonitor,
)


def monitor(db, identifier):
    return db.get(ServiceMonitor, identifier)


def prediction(db, identifier):
    return db.get(PredictionTask, identifier)


def target(db, identifier, server_id, native=None):
    try:
        row = db.get(Container, UUID(str(identifier))) if identifier else None
    except ValueError:
        row = None
    if not row and native and server_id:
        matches = db.scalars(
            select(Container).where(
                Container.server_id == server_id,
                Container.docker_id == native,
                Container.deleted_at.is_(None),
            )
        ).all()
        row = matches[0] if len(matches) == 1 else None
    return (
        row
        if row and not row.deleted_at and (not server_id or row.server_id == server_id)
        else None
    )


def server(db, identifier):
    row = db.get(Server, identifier) if identifier else None
    return row if row and not row.deleted_at else None


def deployments(db, container_id, occurred_at):
    return db.scalars(
        select(ApplicationDeployment).where(
            ApplicationDeployment.container_id == container_id,
            ApplicationDeployment.deleted_at.is_(None),
            ApplicationDeployment.started_at <= occurred_at,
            (ApplicationDeployment.ended_at.is_(None))
            | (ApplicationDeployment.ended_at > occurred_at),
        )
    ).all()


def applications_for_endpoint(db, identifier):
    return db.scalars(
        select(Application)
        .join(LLMServiceBinding)
        .where(
            LLMServiceBinding.llm_endpoint_id == identifier,
            LLMServiceBinding.deleted_at.is_(None),
            Application.deleted_at.is_(None),
            Application.enabled.is_(True),
        )
    ).all()


def environments(db, application_id):
    return set(
        db.scalars(
            select(Server.environment)
            .join(Container)
            .where(
                Container.application_id == application_id,
                Container.deleted_at.is_(None),
                Server.deleted_at.is_(None),
            )
        ).all()
    )


def connections(db, application_id, environment):
    return db.scalars(
        select(ServiceDatabase)
        .join(Application)
        .where(
            ServiceDatabase.application_id == application_id,
            ServiceDatabase.environment == environment,
            ServiceDatabase.enabled.is_(True),
            ServiceDatabase.deleted_at.is_(None),
            Application.deleted_at.is_(None),
            Application.enabled.is_(True),
        )
        .order_by(ServiceDatabase.id)
    ).all()
