"""Database access; transaction ownership stays with the caller."""

from sqlalchemy import text


def ready_query(db):
    db.execute(text("SELECT 1"))
    revision = db.scalar(text("SELECT version_num FROM alembic_version"))
    return revision


def system_status_query(db):
    db.execute(text("SELECT 1"))
    revision = db.scalar(text("SELECT version_num FROM alembic_version"))
    return revision
