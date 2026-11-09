from fastapi import HTTPException

import app.repositories.diagnosis.management as _repository
from app.models import AgentAnalysis, Incident
from app.services.diagnosis.analyses import enqueue, latest
from app.services.pagination import page
from app.services.records import active_record


def analyze(incident_id, body, response, db, user):
    row, created = enqueue(db, incident_id, user, body.force)
    if not created and row.status == "completed":
        response.status_code = 200
    return row


def current(incident_id, db, user):
    active_record(db, Incident, incident_id)
    row = latest(db, incident_id)
    if row is None:
        raise HTTPException(404, "No analysis for this incident")
    return row


def history(response, incident_id, db, user, offset=0, limit=50):
    active_record(db, Incident, incident_id)
    return page(
        db,
        _repository.history_query(incident_id),
        response,
        offset,
        limit,
    )


def detail(analysis_id, db, user):
    return active_record(db, AgentAnalysis, analysis_id)
