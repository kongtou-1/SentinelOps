"""Shared record operations; callers retain transaction ownership."""

from sqlalchemy import select


def get_active(db, model, record_id):
    record = db.scalar(select(model).where(model.id == record_id, model.deleted_at.is_(None)))
    return record


def add(db, record):
    db.add(record)


def refresh(db, record):
    db.refresh(record)
