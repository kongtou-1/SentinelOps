from fastapi import HTTPException, Response

import app.repositories.reports.management as _repository
from app.models import PostmortemReport
from app.schemas.reports import (
    ReportResourceContext,
    ReportResponse,
)
from app.services.executions.approvals import incident_rows
from app.services.pagination import page
from app.services.records import active_record
from app.services.reports.logic import (
    approved_export,
    delete_report,
    edit,
    enqueue_delivery,
    generate,
    review,
    revision,
)
from app.services.reports.resources import resource_context


def detail(db, row):
    result = ReportResponse.model_validate(row)
    current = revision(db, row)
    result.content = current.content if current else None
    result.resource_context = ReportResourceContext.model_validate(
        resource_context(db, row.snapshot)
    )
    return result


def create(incident_id, body, request, response, db, user):
    row, created = generate(db, incident_id, user, body.force, request)
    response.status_code = 202 if created and row.status == "queued" else 200
    return detail(db, row)


def history(response, incident_id, db, user, offset=0, limit=50):
    return [
        detail(db, r)
        for r in incident_rows(db, PostmortemReport, incident_id, offset, limit, response)
    ]


def get_report(identifier, db, user):
    return detail(db, active_record(db, PostmortemReport, identifier))


def revisions(response, identifier, db, user, offset=0, limit=50):
    active_record(db, PostmortemReport, identifier)
    return page(
        db,
        _repository.revisions_query(identifier),
        response,
        offset,
        limit,
    )


def update(identifier, body, request, db, user):
    return detail(db, edit(db, identifier, body, user, request))


def remove(identifier, request, db, user):
    delete_report(db, identifier, user, request)


def review_report(identifier, body, request, db, user):
    return detail(db, review(db, identifier, body, user, request))


def export(identifier, db, user, format):
    row = active_record(db, PostmortemReport, identifier)
    if row.status != "approved":
        raise HTTPException(409, "Only approved reports can be exported")
    payload = approved_export(db, row, format)
    media = {
        "pdf": "application/pdf",
        "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "json": "application/json",
    }[format]
    suffix = "-evidence.json" if format == "json" else f".{format}"
    return Response(
        payload,
        media_type=media,
        headers={
            "Content-Disposition": (
                f'attachment; filename="postmortem-{row.id}-v{row.revision}{suffix}"'
            )
        },
    )


def send(identifier, body, request, db, user):
    return enqueue_delivery(db, identifier, body, user, request)


def deliveries(response, identifier, db, user, offset=0, limit=50):
    active_record(db, PostmortemReport, identifier)
    return page(
        db,
        _repository.deliveries_query(identifier),
        response,
        offset,
        limit,
    )
