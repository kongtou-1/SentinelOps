"""Shared HTTP credentials, separate from query and task configuration."""

import json
from datetime import UTC, datetime
from types import SimpleNamespace

from fastapi import HTTPException
from sqlalchemy import select

from app.core.secrets import cipher
from app.models import InspectionDataSource, InspectionHTTPConnection
from app.repositories import inspections as repository
from app.repositories.notifications import audit
from app.schemas.inspections import InspectionHTTPConnectionResponse


def active(db, identifier, lock=False):
    query = select(InspectionHTTPConnection).where(
        InspectionHTTPConnection.id == identifier, InspectionHTTPConnection.deleted_at.is_(None)
    )
    if lock:
        query = query.with_for_update()
    row = db.scalar(query.execution_options(populate_existing=True))
    if not row:
        raise HTTPException(404, "inspection_connection_unavailable")
    return row


def listing():
    return (
        select(InspectionHTTPConnection)
        .where(InspectionHTTPConnection.deleted_at.is_(None))
        .order_by(InspectionHTTPConnection.name, InspectionHTTPConnection.id)
    )


def response(row):
    return InspectionHTTPConnectionResponse(
        **{
            key: getattr(row, key)
            for key in InspectionHTTPConnectionResponse.model_fields
            if key != "headers_configured"
        },
        headers_configured=bool(row.encrypted_headers),
    )


def references(identifier):
    return select(InspectionDataSource).where(
        InspectionDataSource.deleted_at.is_(None),
        InspectionDataSource.source_config["http_connection_id"].as_string() == str(identifier),
    )


def configure(db, body, user, identifier=None):
    row = active(db, identifier, lock=True) if identifier else InspectionHTTPConnection()
    if identifier and body.expected_version != row.version:
        raise HTTPException(409, "inspection_connection_version_changed")
    if body.application_id:
        application = repository.application(db, body.application_id)
        if not application or application.deleted_at or not application.enabled:
            raise HTTPException(409, "inspection_service_unavailable")
    changed = identifier and row.base_url != body.base_url
    linked = db.scalars(references(identifier)).all() if identifier else []
    if linked and (
        row.application_id != body.application_id or row.environment != body.environment
    ):
        raise HTTPException(409, "inspection_connection_scope_in_use")
    row.name, row.application_id, row.environment, row.base_url, row.enabled = (
        body.name,
        body.application_id,
        body.environment,
        body.base_url,
        body.enabled,
    )
    if body.headers is not None:
        headers = {key: value.get_secret_value() for key, value in body.headers.items()}
        row.encrypted_headers = (
            cipher().encrypt(json.dumps(headers).encode()).decode() if headers else None
        )
    elif changed:
        row.encrypted_headers = None
    row.version = row.version + 1 if identifier else 1
    # Existing source-version guards also revoke in-flight reads after credential/address edits.
    for source in linked:
        source.version += 1
    db.add(row)
    db.flush()
    audit(
        db,
        user,
        "inspection_connection_configured",
        "inspection_http_connection",
        row.id,
        {"version": row.version},
    )
    db.commit()
    return response(row)


def remove(db, identifier, user):
    row = active(db, identifier, lock=True)
    if db.scalar(references(identifier).limit(1)):
        raise HTTPException(409, "inspection_connection_in_use")
    row.deleted_at = row.updated_at = datetime.now(UTC)
    audit(db, user, "inspection_connection_deleted", "inspection_http_connection", row.id)
    db.commit()


def effective(db, row):
    identifier = row.source_config.get("http_connection_id")
    if not identifier:
        return row
    connection = active(db, identifier)
    if (
        not connection.enabled
        or connection.environment != row.environment
        or (connection.application_id and connection.application_id != row.application_id)
    ):
        raise HTTPException(409, "inspection_connection_scope_unavailable")
    # This is a transient view, never a second plaintext credential store.
    config = {**row.source_config, "http_connection_id": None, "url": connection.base_url}
    return SimpleNamespace(
        id=row.id,
        version=row.version,
        application_id=row.application_id,
        environment=row.environment,
        enabled=row.enabled,
        source_kind=row.source_kind,
        source_config=config,
        encrypted_headers=connection.encrypted_headers,
    )
