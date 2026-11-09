"""Persistence for reviewed definitions and version-scoped metric projections."""

from __future__ import annotations

from sqlalchemy import func, or_, select, update

from app.models import AskDataDefinition, User
from app.models.askdata import ONTOLOGY_TABLES

_KIND_TABLE = {"term": "term", "attribute": "column_attribute", "metric": "sql_attribute"}
_KEY_COLUMN = {"term": "name", "attribute": "name", "metric": "name"}


def _table(kind):
    return ONTOLOGY_TABLES[_KIND_TABLE[kind]]


def _active(table):
    return table.c.deleted_at.is_(None)


def definitions(db, version_id, *, review_required=None, kind=None, offset=0, limit=50):
    conditions = [
        AskDataDefinition.model_version_id == version_id,
        AskDataDefinition.deleted_at.is_(None),
    ]
    if review_required is not None:
        conditions.append(AskDataDefinition.review_required == review_required)
    if kind:
        conditions.append(AskDataDefinition.object_kind == kind)
    statement = select(AskDataDefinition).where(*conditions)
    total = int(
        db.scalar(
            select(func.count())
            .select_from(statement.subquery())
            .execution_options(access_internal=True)
        )
        or 0
    )
    pending = int(
        db.scalar(
            select(func.count())
            .select_from(AskDataDefinition)
            .where(
                AskDataDefinition.model_version_id == version_id,
                AskDataDefinition.deleted_at.is_(None),
                AskDataDefinition.review_required.is_(True),
            )
            .execution_options(access_internal=True)
        )
        or 0
    )
    rows = list(
        db.scalars(
            statement.order_by(
                AskDataDefinition.review_required.desc(),
                AskDataDefinition.object_kind,
                AskDataDefinition.object_key,
            )
            .offset(offset)
            .limit(limit)
            .execution_options(access_internal=True)
        )
    )
    return rows, total, pending


def definition(db, version_id, definition_id):
    return db.scalar(
        select(AskDataDefinition)
        .where(
            AskDataDefinition.id == definition_id,
            AskDataDefinition.model_version_id == version_id,
            AskDataDefinition.deleted_at.is_(None),
        )
        .execution_options(access_internal=True)
    )


def definition_for(db, version_id, kind, key):
    return db.scalar(
        select(AskDataDefinition)
        .where(
            AskDataDefinition.model_version_id == version_id,
            AskDataDefinition.object_kind == kind,
            AskDataDefinition.object_key == key,
            AskDataDefinition.deleted_at.is_(None),
        )
        .execution_options(access_internal=True)
    )


def carry_candidates(db, version_id):
    """Reviewed or manually edited rows that a rebuild must preserve."""
    rows = db.scalars(
        select(AskDataDefinition)
        .where(
            AskDataDefinition.model_version_id == version_id,
            AskDataDefinition.deleted_at.is_(None),
        )
        .execution_options(access_internal=True, populate_existing=True)
    ).all()
    return [row for row in rows if row.certified or row.payload]


def save(db, row):
    db.add(row)
    return row


def actor_names(db, identifiers):
    if not identifiers:
        return {}
    return {
        identifier: name
        for identifier, name in db.execute(
            select(User.id, User.username)
            .where(User.id.in_(identifiers))
            .execution_options(access_internal=True)
        ).all()
    }


def object_id(db, version_id, kind, key):
    """Resolve the upstream object's stable key inside one model version."""
    table = _table(kind)
    return db.scalar(
        select(table.c.id)
        .where(
            table.c.model_version_id == version_id,
            table.c[_KEY_COLUMN[kind]] == key,
            _active(table),
        )
        .order_by(table.c.id)
        .limit(1)
        .execution_options(access_internal=True)
    )


def apply(db, version_id, kind, identifier, payload):
    """Write reviewed fields back to the version's upstream object."""
    table = _table(kind)
    values = {}
    if "description" in payload and "description" in table.c:
        values["description"] = payload["description"] or ""
    if kind == "term" and "synonyms" in payload and "synonyms" in table.c:
        values["synonyms"] = list(payload["synonyms"] or [])
    if kind == "metric" and "expression" in payload and "expression" in table.c:
        values["expression"] = payload["expression"] or ""
    if "certified" in payload:
        if kind == "term":
            for column in ("name_certified", "description_certified"):
                if column in table.c:
                    values[column] = bool(payload["certified"])
        elif "certified" in table.c:
            values["certified"] = bool(payload["certified"])
    if "edited" in table.c:
        values["edited"] = True
    if not values:
        return False
    result = db.execute(
        update(table)
        .where(
            table.c.model_version_id == version_id,
            table.c.id == identifier,
            _active(table),
        )
        .values(**values)
        .execution_options(access_internal=True)
    )
    return bool(result.rowcount)


def metrics(db, version_id, search="", offset=0, limit=50):
    attribute = ONTOLOGY_TABLES["sql_attribute"]
    conditions = [attribute.c.model_version_id == version_id, _active(attribute)]
    if search:
        pattern = f"%{search.strip()}%"
        conditions.append(
            or_(
                attribute.c.name.ilike(pattern),
                func.coalesce(attribute.c.description, "").ilike(pattern),
            )
        )
    total = int(
        db.scalar(
            select(func.count())
            .select_from(attribute)
            .where(*conditions)
            .execution_options(access_internal=True)
        )
        or 0
    )
    rows = db.execute(
        select(
            attribute.c.id,
            attribute.c.name,
            attribute.c.description,
            attribute.c.expression,
            attribute.c.source,
            attribute.c.certified,
        )
        .where(*conditions)
        .order_by(attribute.c.name, attribute.c.id)
        .offset(offset)
        .limit(limit)
        .execution_options(access_internal=True)
    ).all()
    return {
        "items": [
            {
                "id": row[0],
                "name": row[1],
                "description": row[2] or "",
                "expression": row[3] or "",
                "source": row[4] or "",
                "certified": bool(row[5]),
            }
            for row in rows
        ],
        "total": total,
    }


def snapshot(db, version_id):
    """Flat, comparable view of the definitions inside one model version."""
    result = {}
    for kind in _KIND_TABLE:
        table = _table(kind)
        columns = [table.c[_KEY_COLUMN[kind]], table.c.description]
        for name in ("expression", "synonyms", "certified"):
            if name in table.c:
                columns.append(table.c[name])
        rows = db.execute(
            select(*columns)
            .where(table.c.model_version_id == version_id, _active(table))
            .execution_options(access_internal=True)
        ).all()
        items = {}
        for row in rows:
            value = {"description": row[1] or ""}
            for index, name in enumerate(
                [name for name in ("expression", "synonyms", "certified") if name in table.c], 2
            ):
                entry = row[index]
                value[name] = list(entry or []) if name == "synonyms" else (
                    bool(entry) if name == "certified" else (entry or "")
                )
            items[str(row[0])] = value
        result[kind] = items
    return result
