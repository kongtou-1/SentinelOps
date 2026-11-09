"""Read-only catalog projections with connection-scoped authorization."""

import json

from fastapi import HTTPException

from app.repositories import askdata as lifecycle
from app.repositories import askdata_catalog as repository
from app.schemas.askdata import (
    AskDataCatalogSummary,
    AskDataRelationshipPage,
    AskDataTableDetail,
    AskDataTablePage,
    AskDataTermDetail,
    AskDataTermPage,
)
from app.services import askdata as settings


def version(db, user, identifier):
    row = lifecycle.model_version(db, identifier)
    if row is None:
        raise HTTPException(404, "问数模型版本不存在")
    settings.authorized_connection(db, user, row.connection_id)
    return row


def summary(db, user, identifier):
    row = version(db, user, identifier)
    data = repository.summary(db, row.id)
    return AskDataCatalogSummary(version_id=row.id, **data)


def tables(db, user, identifier, search="", schema_name=None, offset=0, limit=50):
    row = version(db, user, identifier)
    return AskDataTablePage(**repository.tables(db, row.id, search, schema_name, offset, limit))


def table(db, user, identifier, table_id):
    row = version(db, user, identifier)
    data = repository.table(db, row.id, table_id)
    if data is None:
        raise HTTPException(404, "数据表不存在")
    for column in data["columns"]:
        column["sample_values"] = _samples(column["sample_values"])
    return AskDataTableDetail(**data)


def terms(db, user, identifier, search="", offset=0, limit=50):
    row = version(db, user, identifier)
    return AskDataTermPage(**repository.terms(db, row.id, search, offset, limit))


def term(db, user, identifier, term_id):
    row = version(db, user, identifier)
    data = repository.term(db, row.id, term_id)
    if data is None:
        raise HTTPException(404, "业务术语不存在")
    return AskDataTermDetail(**data)


def relationships(db, user, identifier, limit=500):
    row = version(db, user, identifier)
    return AskDataRelationshipPage(**repository.relationships(db, row.id, limit))


def _samples(value):
    if not value:
        return []
    if isinstance(value, list):
        return value
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return [value]
    return parsed if isinstance(parsed, list) else [parsed]
