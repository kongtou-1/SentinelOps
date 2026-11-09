import json

from fastapi import Response

import app.repositories.repairs.management as _repository
from app.models import Incident, RepairHandoff
from app.services.pagination import page
from app.services.records import active_record
from app.services.repairs.handoffs import create, export_value, markdown, record_result, verify


def generate(incident_id, body, db, user, request):
    return create(db, incident_id, body, user, request)


def history(incident_id, db, user, response, offset=0, limit=10):
    active_record(db, Incident, incident_id)
    return page(
        db,
        _repository.history_query(incident_id),
        response,
        offset,
        limit,
    )


def detail(identifier, db, user):
    return active_record(db, RepairHandoff, identifier)


def result(identifier, body, db, user, request):
    return record_result(db, identifier, body, user, request)


def verification(identifier, body, db, user, request):
    return verify(db, identifier, body, user, request)


def export(identifier, db, user, format="markdown"):
    row = active_record(db, RepairHandoff, identifier)
    content = (
        json.dumps(export_value(row), ensure_ascii=False, indent=2)
        if format == "json"
        else markdown(row)
    )
    extension = "json" if format == "json" else "md"
    return Response(
        content,
        media_type="application/json" if format == "json" else "text/markdown",
        headers={
            "Content-Disposition": f'attachment; filename="repair-{row.id}.{extension}"',
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )
