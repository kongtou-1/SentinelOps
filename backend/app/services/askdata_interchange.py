"""Import and export of ask-data semantic assets.

The document carries the platform-owned semantic layer: reviewed definitions,
code rules, zones, tags, business rules, sensitive-field policies and
supplementary definitions. It never contains connection credentials, prompts
sent to a model, saved analyses or run results.

Import is two-phase: a preview records the document and a per-object plan, then
apply re-reads the plan and writes it in one transaction. Objects are matched by
their natural key, so re-importing the same document is a no-op.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from fastapi import HTTPException
from fastapi.responses import Response
from sqlalchemy import select

from app.models import (
    AskDataCodeRule,
    AskDataDefinition,
    AskDataImport,
    AskDataRule,
    AskDataSensitiveField,
    AskDataSupplement,
    AskDataTag,
    AskDataTagTarget,
    AskDataZone,
)
from app.models.askdata import ONTOLOGY_TABLES
from app.schemas.askdata_stage5 import (
    AskDataImportApplyInput,
    AskDataImportInput,
    AskDataImportItem,
    AskDataImportPreview,
)
from app.services import askdata as settings

FORMAT = "sentinelops.askdata.model"
FORMAT_VERSION = 1
UPSTREAM_COMMIT = "c4495670947eb7b4616f0dd9900e5ced2a48ca1e"
MAX_ITEMS = 500


def _asset_rows(rows, fields):
    return [{field: getattr(row, field) for field in fields} for row in rows]


def export_document(db, user, connection_id):
    connection = settings.authorized_connection(db, user, connection_id, "askdata:read")
    configuration = settings.repository.configuration(db, connection_id)
    version_id = configuration.active_model_version_id if configuration else None
    if version_id is None:
        version = next(
            (row for row in settings.repository.versions(db, connection_id) if row.status == "ready"),
            None,
        )
        version_id = version.id if version else None

    definitions = []
    code_rules = []
    if version_id:
        definitions = db.scalars(
            select(AskDataDefinition)
            .where(
                AskDataDefinition.model_version_id == version_id,
                AskDataDefinition.deleted_at.is_(None),
            )
            .order_by(AskDataDefinition.object_kind, AskDataDefinition.object_key)
            .execution_options(access_internal=True)
        ).all()
        code_rules = db.scalars(
            select(AskDataCodeRule)
            .where(
                AskDataCodeRule.model_version_id == version_id,
                AskDataCodeRule.deleted_at.is_(None),
                AskDataCodeRule.superseded.is_(False),
            )
            .order_by(AskDataCodeRule.path, AskDataCodeRule.start_line)
            .execution_options(access_internal=True)
        ).all()

    zones = db.scalars(
        select(AskDataZone)
        .where(AskDataZone.connection_id == connection_id, AskDataZone.deleted_at.is_(None))
        .execution_options(access_internal=True)
    ).all()
    tags = db.scalars(
        select(AskDataTag)
        .where(AskDataTag.connection_id == connection_id, AskDataTag.deleted_at.is_(None))
        .execution_options(access_internal=True)
    ).all()
    tag_targets = db.scalars(
        select(AskDataTagTarget)
        .where(
            AskDataTagTarget.connection_id == connection_id,
            AskDataTagTarget.deleted_at.is_(None),
        )
        .execution_options(access_internal=True)
    ).all()
    rules = db.scalars(
        select(AskDataRule)
        .where(AskDataRule.connection_id == connection_id, AskDataRule.deleted_at.is_(None))
        .execution_options(access_internal=True)
    ).all()
    sensitive = db.scalars(
        select(AskDataSensitiveField)
        .where(
            AskDataSensitiveField.connection_id == connection_id,
            AskDataSensitiveField.deleted_at.is_(None),
        )
        .execution_options(access_internal=True)
    ).all()
    supplements = db.scalars(
        select(AskDataSupplement)
        .where(
            AskDataSupplement.connection_id == connection_id,
            AskDataSupplement.deleted_at.is_(None),
        )
        .execution_options(access_internal=True)
    ).all()

    document = {
        "format": FORMAT,
        "version": FORMAT_VERSION,
        "upstream": {"repository": "NVIDIA/auto-ontology", "commit": UPSTREAM_COMMIT},
        "exported_at": datetime.now(UTC).isoformat(),
        "connection": {
            "id": str(connection.id),
            "name": connection.name,
            "engine": connection.engine,
            "database": connection.database,
            "environment": connection.environment,
        },
        "model_version_id": str(version_id) if version_id else None,
        "assets": {
            "definitions": [
                {
                    "object_kind": row.object_kind,
                    "object_key": row.object_key,
                    "object_id": row.object_id,
                    "payload": row.payload,
                    "certified": row.certified,
                    "source_fingerprint": row.source_fingerprint,
                }
                for row in definitions
            ],
            "code_rules": [
                {
                    "language": row.language,
                    "path": row.path,
                    "start_line": row.start_line,
                    "end_line": row.end_line,
                    "rule_kind": row.rule_kind,
                    "object_key": row.object_key,
                    "expression": row.expression,
                    "sql_text": row.sql_text,
                    "detail": row.detail,
                    "parse_state": row.parse_state,
                    "fingerprint": row.fingerprint,
                    "certified": row.certified,
                }
                for row in code_rules
            ],
            "zones": _asset_rows(zones, ("name", "description", "targets", "enabled")),
            "tags": [
                {
                    "name": row.name,
                    "color": row.color,
                    "description": row.description,
                    "targets": [
                        {"object_kind": target.object_kind, "object_key": target.object_key}
                        for target in tag_targets
                        if target.tag_id == row.id
                    ],
                }
                for row in tags
            ],
            "rules": _asset_rows(
                rules,
                ("name", "description", "kind", "target", "expression", "unit", "priority", "enabled"),
            ),
            "sensitive_fields": _asset_rows(
                sensitive,
                (
                    "object_key",
                    "category",
                    "policy",
                    "mask_strategy",
                    "mask_argument",
                    "sampling",
                    "note",
                    "enabled",
                ),
            ),
            "supplements": _asset_rows(
                supplements, ("kind", "term", "definition", "aliases", "applies_to", "enabled")
            ),
        },
    }
    payload = json.dumps(document, ensure_ascii=False, indent=2).encode("utf-8")
    return Response(
        content=payload,
        media_type="application/json; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="askdata-{connection_id}.json"'
        },
    )


def _tag_targets_by_name(db, connection_id):
    rows = db.scalars(
        select(AskDataTagTarget)
        .where(
            AskDataTagTarget.connection_id == connection_id,
            AskDataTagTarget.deleted_at.is_(None),
        )
        .execution_options(access_internal=True)
    ).all()
    tags = {
        row.id: row.name
        for row in db.scalars(
            select(AskDataTag)
            .where(AskDataTag.connection_id == connection_id, AskDataTag.deleted_at.is_(None))
            .execution_options(access_internal=True)
        )
    }
    grouped: dict[str, list] = {}
    for row in rows:
        name = tags.get(row.tag_id)
        if name:
            grouped.setdefault(name, []).append(
                {"object_kind": row.object_kind, "object_key": row.object_key}
            )
    return grouped


def _target_version(db, connection_id):
    configuration = settings.repository.configuration(db, connection_id)
    if configuration and configuration.active_model_version_id:
        return configuration.active_model_version_id
    versions = settings.repository.versions(db, connection_id)
    for version in versions:
        if version.status == "ready":
            return version.id
    return None


def _catalog_keys(db, version_id):
    if version_id is None:
        return set()
    term = ONTOLOGY_TABLES["term"]
    attribute = ONTOLOGY_TABLES["sql_attribute"]
    terms = db.execute(
        select(term.c.name)
        .where(term.c.model_version_id == version_id, term.c.deleted_at.is_(None))
        .execution_options(access_internal=True)
    ).all()
    metrics = db.execute(
        select(attribute.c.name)
        .where(
            attribute.c.model_version_id == version_id,
            attribute.c.deleted_at.is_(None),
        )
        .execution_options(access_internal=True)
    ).all()
    return {("term", row[0]) for row in terms} | {("metric", row[0]) for row in metrics}


def _validate(document):
    if not isinstance(document, dict):
        raise HTTPException(422, "导入文件必须是 JSON 对象")
    if document.get("format") != FORMAT:
        raise HTTPException(422, "导入文件格式不支持")
    if int(document.get("version") or 0) != FORMAT_VERSION:
        raise HTTPException(422, "导入文件版本不支持")
    assets = document.get("assets")
    if not isinstance(assets, dict):
        raise HTTPException(422, "导入文件缺少业务资产")


def _plan(db, connection_id, document):
    _validate(document)
    assets = document["assets"]
    version_id = _target_version(db, connection_id)
    catalog = _catalog_keys(db, version_id)
    actions, warnings = [], []

    existing_definitions = {}
    if version_id:
        for row in db.scalars(
            select(AskDataDefinition)
            .where(
                AskDataDefinition.model_version_id == version_id,
                AskDataDefinition.deleted_at.is_(None),
            )
            .execution_options(access_internal=True)
        ):
            existing_definitions[(row.object_kind, row.object_key)] = row
    for item in assets.get("definitions") or []:
        kind, key = item.get("object_kind"), item.get("object_key")
        if kind not in ("term", "attribute", "metric") or not key:
            continue
        current = existing_definitions.get((kind, key))
        mapped = (kind, key) in catalog
        if version_id is None:
            actions.append(_action("definitions", f"{kind}:{key}", "skip", "没有可用的模型版本"))
            continue
        if current is None:
            actions.append(
                _action(
                    "definitions",
                    f"{kind}:{key}",
                    "create",
                    "" if mapped else "目标模型中没有对应对象",
                    {**item, "review_required": not mapped},
                )
            )
        elif (current.payload or {}) == (item.get("payload") or {}):
            actions.append(_action("definitions", f"{kind}:{key}", "skip", "内容一致"))
        else:
            actions.append(
                _action(
                    "definitions",
                    f"{kind}:{key}",
                    "conflict",
                    "目标已有不同定义",
                    {**item},
                )
            )

    existing_rules = {
        row.fingerprint: row
        for row in db.scalars(
            select(AskDataCodeRule)
            .where(
                AskDataCodeRule.model_version_id == version_id,
                AskDataCodeRule.deleted_at.is_(None),
            )
            .execution_options(access_internal=True)
        )
    } if version_id else {}
    for item in assets.get("code_rules") or []:
        fingerprint = str(item.get("fingerprint") or "")
        if not fingerprint or not item.get("path"):
            continue
        if version_id is None:
            actions.append(_action("code_rules", fingerprint, "skip", "没有可用的模型版本"))
        elif fingerprint in existing_rules:
            actions.append(_action("code_rules", fingerprint, "skip", "同源码规则已存在"))
        else:
            actions.append(
                _action("code_rules", fingerprint, "create", "导入后需人工复核", item)
            )

    zones = {
        row.name: row
        for row in db.scalars(
            select(AskDataZone)
            .where(AskDataZone.connection_id == connection_id, AskDataZone.deleted_at.is_(None))
            .execution_options(access_internal=True)
        )
    }
    for item in assets.get("zones") or []:
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        actions.append(_upsert_action("zones", name, item, zones.get(name), "name"))

    tag_targets = _tag_targets_by_name(db, connection_id)
    tags = {
        row.name: row
        for row in db.scalars(
            select(AskDataTag)
            .where(AskDataTag.connection_id == connection_id, AskDataTag.deleted_at.is_(None))
            .execution_options(access_internal=True)
        )
    }
    for item in assets.get("tags") or []:
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        payload = {**item, "targets": item.get("targets") or tag_targets.get(name, [])}
        actions.append(_upsert_action("tags", name, payload, tags.get(name), "name"))

    rules = {
        row.name: row
        for row in db.scalars(
            select(AskDataRule)
            .where(AskDataRule.connection_id == connection_id, AskDataRule.deleted_at.is_(None))
            .execution_options(access_internal=True)
        )
    }
    for item in assets.get("rules") or []:
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        actions.append(_upsert_action("rules", name, item, rules.get(name), "name"))

    sensitive = {
        row.object_key: row
        for row in db.scalars(
            select(AskDataSensitiveField)
            .where(
                AskDataSensitiveField.connection_id == connection_id,
                AskDataSensitiveField.deleted_at.is_(None),
            )
            .execution_options(access_internal=True)
        )
    }
    for item in assets.get("sensitive_fields") or []:
        key = str(item.get("object_key") or "").strip()
        if not key:
            continue
        actions.append(
            _upsert_action("sensitive_fields", key, item, sensitive.get(key), "object_key")
        )

    supplements = {
        row.term: row
        for row in db.scalars(
            select(AskDataSupplement)
            .where(
                AskDataSupplement.connection_id == connection_id,
                AskDataSupplement.deleted_at.is_(None),
            )
            .execution_options(access_internal=True)
        )
    }
    for item in assets.get("supplements") or []:
        term = str(item.get("term") or "").strip()
        if not term:
            continue
        actions.append(_upsert_action("supplements", term, item, supplements.get(term), "term"))

    if version_id is None:
        warnings.append("目标连接没有可用模型版本，业务定义与代码规则不会导入")
    if not catalog and version_id:
        warnings.append("目标模型目录为空，导入的业务定义将标记待复核")
    return actions, warnings, version_id


def _action(asset, key, action, reason, payload=None):
    return {"asset": asset, "key": key, "action": action, "reason": reason, "payload": payload}


def _upsert_action(asset, key, item, current, field):
    if current is None:
        return _action(asset, key, "create", "", item)
    columns = {column.name for column in current.__table__.columns}
    same = all(
        getattr(current, name) == value
        for name, value in item.items()
        if name in columns
    )
    if same:
        return _action(asset, key, "skip", "内容一致")
    return _action(asset, key, "conflict", "目标已有不同内容", item)


def _counts(actions):
    counts = {"create": 0, "update": 0, "skip": 0, "conflict": 0}
    for action in actions:
        counts[action["action"]] = counts.get(action["action"], 0) + 1
    return counts


def _preview(actions, warnings, version_id, identifier):
    items = [
        AskDataImportItem(
            asset=action["asset"], key=action["key"], action=action["action"], reason=action["reason"]
        )
        for action in actions[:MAX_ITEMS]
    ]
    return AskDataImportPreview(
        import_id=identifier,
        format=FORMAT,
        upstream_commit=str(UPSTREAM_COMMIT),
        counts=_counts(actions),
        items=items,
        warnings=warnings,
        model_version_id=version_id,
    )


def preview(db, user, connection_id, body: AskDataImportInput):
    settings.authorized_connection(db, user, connection_id, "askdata:manage")
    actions, warnings, version_id = _plan(db, connection_id, body.document)
    row = AskDataImport(
        connection_id=connection_id,
        model_version_id=version_id,
        requested_by=user.id,
        filename=body.filename or "askdata-model.json",
        status="previewed",
        document=body.document,
        report={
            "counts": _counts(actions),
            "warnings": warnings,
            "items": [
                {k: action[k] for k in ("asset", "key", "action", "reason")}
                for action in actions[:MAX_ITEMS]
            ],
        },
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return _preview(actions, warnings, version_id, row.id)


def apply(db, user, connection_id, identifier, body: AskDataImportApplyInput):
    connection = settings.authorized_connection(db, user, connection_id, "askdata:manage")
    row = db.get(AskDataImport, identifier)
    if row is None or row.deleted_at or row.connection_id != connection.id:
        raise HTTPException(404, "导入记录不存在")
    if row.status != "previewed":
        raise HTTPException(409, "该导入已执行或已失败")
    actions, warnings, version_id = _plan(db, connection_id, row.document)
    written = 0
    try:
        for action in actions:
            if action["action"] == "conflict" and body.conflict_policy == "overwrite":
                _write(db, connection.id, version_id, action, overwrite=True)
                written += 1
            elif action["action"] == "create":
                _write(db, connection.id, version_id, action, overwrite=False)
                written += 1
        row.status = "applied"
        row.applied_at = datetime.now(UTC)
        row.conflict_policy = body.conflict_policy
        row.report = {
            **(row.report or {}),
            "applied": written,
            "counts": _counts(actions),
            "warnings": warnings,
            "conflict_policy": body.conflict_policy,
        }
        from app.repositories import service_databases as connection_repository

        connection_repository.audit(db, connection, user, "askdata_model_imported")
        db.commit()
    except HTTPException:
        db.rollback()
        raise
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        row = db.get(AskDataImport, identifier)
        if row is not None:
            row.status = "failed"
            row.report = {**(row.report or {}), "error": exc.__class__.__name__}
            db.commit()
        raise HTTPException(500, "导入失败，未写入任何记录") from exc
    db.refresh(row)
    return row


def _write(db, connection_id, version_id, action, *, overwrite):
    payload = action["payload"] or {}
    asset = action["asset"]
    if asset == "definitions":
        if version_id is None:
            return
        current = db.scalar(
            select(AskDataDefinition)
            .where(
                AskDataDefinition.model_version_id == version_id,
                AskDataDefinition.object_kind == payload["object_kind"],
                AskDataDefinition.object_key == payload["object_key"],
                AskDataDefinition.deleted_at.is_(None),
            )
            .execution_options(access_internal=True)
        )
        if current is None:
            current = AskDataDefinition(
                connection_id=connection_id,
                model_version_id=version_id,
                object_kind=payload["object_kind"],
                object_key=payload["object_key"],
            )
            db.add(current)
        elif not overwrite:
            return
        current.object_id = payload.get("object_id")
        current.payload = payload.get("payload") or {}
        current.certified = bool(payload.get("certified"))
        current.source_fingerprint = str(payload.get("source_fingerprint") or "")
        current.review_required = bool(payload.get("review_required"))
        return
    if asset == "code_rules":
        if version_id is None:
            return
        db.add(
            AskDataCodeRule(
                connection_id=connection_id,
                model_version_id=version_id,
                language=str(payload.get("language") or "other"),
                path=str(payload.get("path") or ""),
                start_line=int(payload.get("start_line") or 0),
                end_line=int(payload.get("end_line") or 0),
                rule_kind=str(payload.get("rule_kind") or "filter"),
                object_key=str(payload.get("object_key") or "")[:300],
                expression=str(payload.get("expression") or ""),
                sql_text=str(payload.get("sql_text") or ""),
                detail=payload.get("detail") or {},
                parse_state=payload.get("parse_state") if payload.get("parse_state") in ("static", "dynamic") else "dynamic",
                source="manual",
                fingerprint=str(payload.get("fingerprint") or ""),
                certified=False,
                review_required=True,
                review_note="导入的源码规则",
            )
        )
        return
    if asset == "zones":
        _upsert(db, AskDataZone, connection_id, "name", payload, overwrite)
    elif asset == "tags":
        tag = _upsert(db, AskDataTag, connection_id, "name", payload, overwrite)
        if tag is not None:
            for target in payload.get("targets") or []:
                exists = db.scalar(
                    select(AskDataTagTarget)
                    .where(
                        AskDataTagTarget.tag_id == tag.id,
                        AskDataTagTarget.object_kind == target.get("object_kind"),
                        AskDataTagTarget.object_key == target.get("object_key"),
                        AskDataTagTarget.deleted_at.is_(None),
                    )
                    .execution_options(access_internal=True)
                )
                if exists is None:
                    db.add(
                        AskDataTagTarget(
                            connection_id=connection_id,
                            tag_id=tag.id,
                            object_kind=str(target.get("object_kind") or "table"),
                            object_key=str(target.get("object_key") or "")[:300],
                        )
                    )
    elif asset == "rules":
        _upsert(db, AskDataRule, connection_id, "name", payload, overwrite)
    elif asset == "sensitive_fields":
        _upsert(db, AskDataSensitiveField, connection_id, "object_key", payload, overwrite)
    elif asset == "supplements":
        _upsert(db, AskDataSupplement, connection_id, "term", payload, overwrite)


FIELDS = {
    AskDataZone: ("name", "description", "targets", "enabled"),
    AskDataTag: ("name", "color", "description"),
    AskDataRule: (
        "name",
        "description",
        "kind",
        "target",
        "expression",
        "unit",
        "priority",
        "enabled",
    ),
    AskDataSensitiveField: (
        "object_key",
        "category",
        "policy",
        "mask_strategy",
        "mask_argument",
        "sampling",
        "note",
        "enabled",
    ),
    AskDataSupplement: ("kind", "term", "definition", "aliases", "applies_to", "enabled"),
}


def _upsert(db, model, connection_id, key_field, payload, overwrite):
    value = payload.get(key_field)
    if not value:
        return None
    row = db.scalar(
        select(model)
        .where(
            model.connection_id == connection_id,
            getattr(model, key_field) == value,
            model.deleted_at.is_(None),
        )
        .execution_options(access_internal=True)
    )
    if row is None:
        row = model(connection_id=connection_id)
        db.add(row)
    elif not overwrite:
        return row
    for field in FIELDS[model]:
        if field in payload and payload[field] is not None:
            setattr(row, field, payload[field])
    return row


def listing(db, user, connection_id, limit=20):
    settings.authorized_connection(db, user, connection_id, "askdata:read")
    return db.scalars(
        select(AskDataImport)
        .where(AskDataImport.connection_id == connection_id, AskDataImport.deleted_at.is_(None))
        .order_by(AskDataImport.created_at.desc())
        .limit(min(limit, 100))
        .execution_options(access_internal=True)
    ).all()
