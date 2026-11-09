"""Database access; transaction ownership stays with the caller."""

from sqlalchemy import select

from app.models import Application, Container, SourceAuditLog


def attach_services_query(containers, db):
    applications = db.scalars(
        select(Application)
        .where(
            Application.deleted_at.is_(None),
            Application.id.in_({c.application_id for c in containers if c.application_id}),
        )
        .order_by(Application.name)
    ).all()
    return applications


def services_query():
    return select(Application).where(Application.deleted_at.is_(None)).order_by(Application.name)


def lock_server_query(db, server):
    db.refresh(server, with_for_update=True)


def members_query(db, server_id):
    inventory = db.scalars(
        select(Container)
        .where(Container.server_id == server_id, Container.deleted_at.is_(None))
        .with_for_update()
    ).all()
    return inventory


def members_query_2(db):
    owners = set(db.scalars(select(Application.id).where(Application.deleted_at.is_(None))))
    return owners


def audit_query(action, container_ids, db, row, server_id, user):
    db.add(
        SourceAuditLog(
            application_id=row.id,
            user_id=user.id,
            action=action,
            detail={
                "server_id": str(server_id),
                "container_ids": [str(v) for v in dict.fromkeys(container_ids)],
            },
        )
    )


def create_service_query(db, row):
    db.add(row)


def update_service_query(db, row):
    db.refresh(row, with_for_update=True)


def assign_service_query(db, row):
    db.refresh(row, with_for_update=True)
