import hashlib
import json
from contextlib import contextmanager
from datetime import UTC, datetime
from types import SimpleNamespace

from fastapi import HTTPException
from sqlalchemy import text

from app.core.secrets import cipher
from app.db.session import engine
from app.integrations import database_readonly as adapter
from app.models import ServiceDatabase
from app.repositories import service_databases as repository
from app.schemas.service_databases import DatabaseResponse, DatabaseSchemaResponse
from app.utils.redaction import redact


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def active(db, identifier):
    row = repository.current(db, identifier)
    if not row or row.deleted_at:
        raise HTTPException(404, "Database connection not found")
    if row.application_id is not None:
        app = repository.application(db, row.application_id)
        if not app or app.deleted_at:
            raise HTTPException(404, "Database connection not found")
    return row


def response(row):
    values = {
        key: getattr(row, key)
        for key in DatabaseResponse.model_fields
        if key not in {"password_configured", "ca_configured"}
    }
    try:
        config = adapter.secrets(row)
    except adapter.DatabaseUnavailable:
        raise HTTPException(503, "credential_unavailable") from None
    return DatabaseResponse(
        **values,
        password_configured=bool(config.get("password")),
        ca_configured=bool(config.get("ca_certificate")),
    )


def listing(db, application_id=None):
    if application_id is not None:
        app = repository.application(db, application_id)
        if not app or app.deleted_at:
            raise HTTPException(404, "Service not found")
    return [response(row) for row in repository.listing(db, application_id)]


def configured(body, row=None):
    secret = adapter.secrets(row) if row else {}
    for key in ("password", "ca_certificate"):
        value = getattr(body, key)
        if value is not None:
            secret[key] = value.get_secret_value()
    values = body.model_dump(
        exclude={"password", "ca_certificate", "connection_id", "application_id"}
    )
    return SimpleNamespace(
        **values, encrypted_config=cipher().encrypt(json.dumps(secret).encode()).decode()
    )


@contextmanager
def connection_lock(identifier):
    # A dedicated platform connection keeps the session lock through the external read.
    key = int.from_bytes(hashlib.sha256(str(identifier).encode()).digest()[:8], signed=True)
    with engine.connect() as connection:
        locked = connection.scalar(text("SELECT pg_try_advisory_lock(:key)"), {"key": key})
        if not locked:
            raise HTTPException(409, "database_busy")
        try:
            yield
        finally:
            connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": key})


def configure(db, application_id, body, user, identifier=None):
    if identifier and "application_id" in body.model_fields_set:
        application_id = body.application_id
    elif body.application_id is not None and body.application_id != application_id:
        raise HTTPException(422, "Database service does not match the selected service")
    if application_id is not None:
        app = repository.application(db, application_id)
        if not app or app.deleted_at:
            raise HTTPException(404, "Service not found")
    row = active(db, identifier) if identifier else ServiceDatabase(application_id=application_id)
    candidate = configured(body, row if identifier else None)
    if not body.enabled:
        candidate.query_enabled = False
    cache = row.schema_cache if identifier else {}
    if candidate.query_enabled:
        for table, columns in body.allowed_tables.items():
            known = {c["name"] for c in cache.get("tables", {}).get(table, {}).get("columns", [])}
            if not set(columns) <= known:
                raise HTTPException(422, "Refresh schema before authorizing columns")
        try:
            with (
                connection_lock(row.id or digest(body.model_dump(mode="json"))),
                adapter.connect(candidate) as remote,
            ):
                if adapter.permissions(candidate, remote) != "verified":
                    raise HTTPException(422, "Read-only permissions are not verified")
        except adapter.DatabaseUnavailable as exc:
            raise HTTPException(422, str(exc)) from None
    old_identity = tuple(
        getattr(row, k, None)
        for k in ("engine", "host", "port", "database", "username", "tls_mode")
    )
    for key, value in vars(candidate).items():
        setattr(row, key, value)
    row.application_id = application_id
    row.config_version = (row.config_version or 0) + 1
    row.permission_status = "verified" if candidate.query_enabled else "unverified"
    row.last_test_at = row.last_test_success = row.last_error_code = None
    if (
        old_identity
        != tuple(
            getattr(row, k) for k in ("engine", "host", "port", "database", "username", "tls_mode")
        )
        or body.password is not None
    ):
        row.schema_cache, row.schema_hash, row.schema_refreshed_at = {}, "", None
        if candidate.query_enabled:
            raise HTTPException(422, "Save connection changes with business queries disabled first")
    repository.save(db, row)
    db.flush()
    repository.audit(db, row, user, "database_configured")
    db.commit()
    db.refresh(row)
    return response(row)


def remove(db, identifier, user):
    row = active(db, identifier)
    row.deleted_at = row.updated_at = datetime.now(UTC)
    row.enabled = row.query_enabled = False
    row.config_version += 1
    repository.audit(db, row, user, "database_deleted")
    db.commit()


def test(db, body, user):
    row = active(db, body.connection_id) if body.connection_id else None
    candidate = configured(body, row)
    result = {
        "success": False,
        "permission_status": "unverified",
        "error_code": None,
        "tested_at": datetime.now(UTC),
    }
    try:
        with (
            connection_lock(body.connection_id or digest(body.model_dump(mode="json"))),
            adapter.connect(candidate) as connection,
        ):
            adapter.fetch(connection, "SELECT 1")
            result["success"] = True
            result["permission_status"] = adapter.permissions(candidate, connection)
    except adapter.DatabaseUnavailable as exc:
        result["error_code"] = str(exc)
    # A changed draft must not overwrite evidence about the saved connection.
    if (
        row
        and all(
            getattr(candidate, k) == getattr(row, k)
            for k in body.model_dump(
                exclude={"password", "ca_certificate", "connection_id", "application_id"}
            )
        )
        and adapter.secrets(candidate) == adapter.secrets(row)
    ):
        row.last_test_at = result["tested_at"]
        row.last_test_success = result["success"]
        row.last_error_code = result["error_code"]
        row.permission_status = result["permission_status"]
        repository.audit(db, row, user, "database_tested")
        db.commit()
    return result


def schema_response(row):
    return DatabaseSchemaResponse(
        schema_hash=row.schema_hash,
        refreshed_at=row.schema_refreshed_at,
        tables=row.schema_cache.get("tables", {}),
        truncated=row.schema_cache.get("truncated", False),
        table_notes=row.table_notes,
        description=row.description,
    )


def refresh_schema(db, identifier, user):
    row = active(db, identifier)
    try:
        with connection_lock(identifier), adapter.connect(row) as connection:
            cache = adapter.schema(row, connection)
        hashed = digest(cache)
        if hashed != row.schema_hash:
            row.config_version += 1
        row.schema_cache, row.schema_hash = cache, hashed
        row.schema_refreshed_at = datetime.now(UTC)
        repository.audit(db, row, user, "database_schema_refreshed")
        db.commit()
        return schema_response(row)
    except adapter.DatabaseUnavailable as exc:
        raise HTTPException(422, str(exc)) from None


def frozen(row):
    return redact(
        {
            "connection_id": str(row.id),
            "application_id": str(row.application_id) if row.application_id else None,
            "name": row.name,
            "engine": row.engine,
            "database": row.database,
            "environment": row.environment,
            "purpose": row.purpose,
            "description": row.description,
            "table_notes": row.table_notes,
            "config_version": row.config_version,
            "schema_hash": row.schema_hash,
            "schema_refreshed_at": row.schema_refreshed_at.isoformat()
            if row.schema_refreshed_at
            else None,
            "schema_truncated": bool(row.schema_cache.get("truncated")),
            "allowed_tables": row.allowed_tables,
            "query_enabled": row.query_enabled,
        }
    )
