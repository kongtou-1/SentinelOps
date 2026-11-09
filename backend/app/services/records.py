"""Shared active-record lookup and simple committed saves."""

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError

from app.repositories import records as _repository


def active_record(db, model, record_id):
    record = _repository.get_active(db, model, record_id)
    if record is None:
        raise HTTPException(404, "Resource not found")
    return record


def save(db, record):
    _repository.add(db, record)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "Resource name or code is already reserved") from None
    _repository.refresh(db, record)
    return record
