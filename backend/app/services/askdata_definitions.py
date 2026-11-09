"""Business-model reviews, version comparison and rebuild-safe carry-over.

Definitions are the platform's durable annotation layer. Upstream ontology
tables are rebuilt into a fresh version, so reviewed descriptions, synonyms,
metric expressions and certification states live here and are re-applied after
each successful build. A changed structure or source snapshot marks them for
review instead of silently discarding them.
"""

from __future__ import annotations

from fastapi import HTTPException

from app.db.session import SessionLocal
from app.models import AskDataDefinition
from app.repositories import askdata as lifecycle
from app.repositories import askdata_definitions as repository
from app.schemas.askdata_definitions import (
    AskDataDefinitionInput,
    AskDataDefinitionPage,
    AskDataDefinitionResponse,
    AskDataDiffItem,
    AskDataMetricPage,
    AskDataVersionDiff,
)
from app.services import askdata as settings
from app.services import service_databases

_REVIEW_SOURCE_CHANGED = "来源结构或代码快照已变化，待复核"
_REVIEW_OBJECT_MISSING = "来源对象已移除，待复核"
_EDITABLE_FIELDS = ("description", "synonyms", "expression", "certified")


def _version(db, user, identifier, permission="askdata:read"):
    row = lifecycle.model_version(db, identifier)
    if row is None:
        raise HTTPException(404, "问数模型版本不存在")
    connection = settings.authorized_connection(db, user, row.connection_id, permission)
    return row, connection


def _fingerprint(connection, version):
    return f"{connection.schema_hash or ''}:{version.source_snapshot_id or ''}"


def _serialize(row, names):
    return AskDataDefinitionResponse(
        id=row.id,
        model_version_id=row.model_version_id,
        object_kind=row.object_kind,
        object_key=row.object_key,
        object_id=row.object_id,
        payload=dict(row.payload or {}),
        certified=bool(row.certified),
        review_required=bool(row.review_required),
        review_note=row.review_note or "",
        edited_by_name=names.get(row.edited_by, ""),
        source_fingerprint=row.source_fingerprint or "",
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def listing(db, user, version_id, *, review_required=None, kind=None, offset=0, limit=50):
    version, _ = _version(db, user, version_id)
    rows, total, pending = repository.definitions(
        db,
        version.id,
        review_required=review_required,
        kind=kind,
        offset=offset,
        limit=limit,
    )
    names = repository.actor_names(db, {row.edited_by for row in rows if row.edited_by})
    return AskDataDefinitionPage(
        items=[_serialize(row, names) for row in rows],
        total=total,
        pending_review=pending,
    )


def upsert(db, user, version_id, body: AskDataDefinitionInput):
    version, connection = _version(db, user, version_id, "askdata:manage")
    identifier = repository.object_id(db, version.id, body.object_kind, body.object_key)
    if identifier is None:
        raise HTTPException(404, "业务定义不存在")
    changes = {}
    for field in _EDITABLE_FIELDS:
        value = getattr(body, field)
        if value is None:
            continue
        if field == "synonyms":
            value = [str(item).strip() for item in value if str(item).strip()][:50]
        changes[field] = value
    row = repository.definition_for(db, version.id, body.object_kind, body.object_key)
    if row is None:
        row = AskDataDefinition(
            connection_id=connection.id,
            model_version_id=version.id,
            object_kind=body.object_kind,
            object_key=body.object_key,
            payload={},
        )
        repository.save(db, row)
    row.object_id = identifier
    row.payload = {**(row.payload or {}), **changes}
    if "certified" in changes:
        row.certified = bool(changes["certified"])
    row.edited_by = user.id
    row.source_fingerprint = _fingerprint(connection, version)
    row.review_required = False
    row.review_note = ""
    repository.apply(db, version.id, body.object_kind, identifier, row.payload)
    service_databases.repository.audit(db, connection, user, "askdata_definition_revised")
    db.commit()
    db.refresh(row)
    return _serialize(row, {user.id: user.username})


def resolve(db, user, version_id, definition_id):
    version, connection = _version(db, user, version_id, "askdata:manage")
    row = repository.definition(db, version.id, definition_id)
    if row is None:
        raise HTTPException(404, "待复核定义不存在")
    identifier = repository.object_id(db, version.id, row.object_kind, row.object_key)
    if identifier is None:
        raise HTTPException(409, "来源对象已变化，无法复核")
    row.object_id = identifier
    row.review_required = False
    row.review_note = ""
    row.source_fingerprint = _fingerprint(connection, version)
    row.edited_by = user.id
    repository.apply(db, version.id, row.object_kind, identifier, row.payload)
    service_databases.repository.audit(db, connection, user, "askdata_definition_reviewed")
    db.commit()
    db.refresh(row)
    return _serialize(row, {user.id: user.username})


def metrics(db, user, version_id, search="", offset=0, limit=50):
    version, _ = _version(db, user, version_id)
    return AskDataMetricPage(**repository.metrics(db, version.id, search, offset, limit))


def diff(db, user, version_id, compare_version_id):
    version, _ = _version(db, user, version_id)
    compare, _ = _version(db, user, compare_version_id)
    if version.connection_id != compare.connection_id:
        raise HTTPException(409, "只能比较同一连接的模型版本")
    current = repository.snapshot(db, version.id)
    previous = repository.snapshot(db, compare.id)
    added, removed, changed = [], [], []
    for kind, items in current.items():
        before_items = previous.get(kind, {})
        for key in sorted(set(items) | set(before_items)):
            before, after = before_items.get(key), items.get(key)
            payload = {
                "object_kind": kind,
                "object_key": key,
                "before": before or {},
                "after": after or {},
            }
            if before is None:
                added.append(AskDataDiffItem(**payload))
            elif after is None:
                removed.append(AskDataDiffItem(**payload))
            elif before != after:
                changed.append(AskDataDiffItem(**payload))
    return AskDataVersionDiff(
        version_id=version.id,
        compare_version_id=compare.id,
        added=added,
        removed=removed,
        changed=changed,
        summary={
            "added": len(added),
            "removed": len(removed),
            "changed": len(changed),
            "terms": len(current["term"]),
            "metrics": len(current["metric"]),
            "compare_terms": len(previous["term"]),
            "compare_metrics": len(previous["metric"]),
        },
    )


def carry_over(connection_id, version_id):
    """Re-apply reviewed definitions after a successful build.

    Runs on its own Session after the build's storage transaction has been
    committed, so the new version's upstream objects are visible here.
    """
    with SessionLocal() as db:
        version = lifecycle.model_version(db, version_id)
        connection = lifecycle.connection(db, connection_id)
        if version is None or connection is None:
            return {"carried": 0, "review": 0}
        fingerprint = _fingerprint(connection, version)
        configuration = lifecycle.configuration(db, connection_id)
        preferred = configuration.active_model_version_id if configuration else None
        versions = lifecycle.versions(db, connection_id)
        usable = [
            item
            for item in versions
            if item.id != version_id and item.status in ("ready", "retired")
        ]
        source = next((item for item in usable if item.id == preferred), None) or (
            usable[0] if usable else None
        )
        if source is None:
            return {"carried": 0, "review": 0}
        carried = review = 0
        for item in repository.carry_candidates(db, source.id):
            identifier = repository.object_id(db, version.id, item.object_kind, item.object_key)
            stale = item.source_fingerprint != fingerprint
            review_required = item.review_required or stale or identifier is None
            note = item.review_note if item.review_required else ""
            if identifier is None:
                note = _REVIEW_OBJECT_MISSING
            else:
                repository.apply(
                    db,
                    version.id,
                    item.object_kind,
                    identifier,
                    {**(item.payload or {}), "certified": item.certified and not review_required},
                )
                if stale:
                    note = _REVIEW_SOURCE_CHANGED
            target = repository.definition_for(db, version.id, item.object_kind, item.object_key)
            if target is None:
                target = AskDataDefinition(
                    connection_id=connection_id,
                    model_version_id=version.id,
                    object_kind=item.object_kind,
                    object_key=item.object_key,
                    payload={},
                )
                repository.save(db, target)
            target.object_id = identifier
            target.payload = dict(item.payload or {})
            target.certified = bool(item.certified)
            target.edited_by = item.edited_by
            target.source_fingerprint = item.source_fingerprint
            target.review_required = review_required
            target.review_note = note
            if target.review_required:
                review += 1
            else:
                carried += 1
        db.commit()
        return {"carried": carried, "review": review, "source_version_id": str(source.id)}
