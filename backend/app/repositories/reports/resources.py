"""Database access; transaction ownership stays with the caller."""

from sqlalchemy import select

from app.models import ApplicationDeployment, Container


def resource_context_asset_query(db, key, model):
    return db.get(model, key)


def resource_context_query(db, server):
    return db.scalars(select(Container).where(Container.server_id == server.id))


def resource_context_query_2(column, db, target):
    return db.scalars(
        select(ApplicationDeployment).where(
            column == target.id,
            ApplicationDeployment.deleted_at.is_(None),
        )
    )
