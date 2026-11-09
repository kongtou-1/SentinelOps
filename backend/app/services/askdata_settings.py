"""Connection-scoped ask-data governance settings.

Zones, tags, business rules, sensitive-field policies and supplementary
definitions are human decisions that outlive a semantic build. Execution
settings (channels, budget, vector model, reranker) live on the same row so a
single revision covers everything an operator can change.

Sensitive-field policies are enforced here through two helpers consumed by the
database connector: sampling drops excluded columns before they reach prompts or
vector rows, and result masking runs on the single executed result before it is
persisted, so the answer, table, chart and CSV all share masked values.
"""

from __future__ import annotations

import hashlib
import time
from datetime import UTC, datetime

from fastapi import HTTPException
from sqlalchemy import delete, select

from app.core.secrets import cipher
from app.models import (
    AskDataConfiguration,
    AskDataRule,
    AskDataSensitiveField,
    AskDataSupplement,
    AskDataTag,
    AskDataTagTarget,
    AskDataZone,
)
from app.repositories import service_databases as connection_repository
from app.schemas.askdata_stage5 import (
    AskDataChannelOption,
    AskDataConnectionTestResponse,
    AskDataExecutionSettingsInput,
    AskDataExecutionSettingsResponse,
    AskDataRuleInput,
    AskDataSensitiveFieldInput,
    AskDataSupplementInput,
    AskDataTagInput,
    AskDataZoneInput,
)
from app.services import askdata as lifecycle

DEFAULT_BUDGET_SECONDS = 180
MASK_MARK = "***"


def vector_signature(model: str, dimension: int) -> str:
    return f"{model or 'default'}:{int(dimension or 0)}"


def default_vector_settings():
    from app.services.knowledge.index import MODEL

    return MODEL, 512


def _connection(db, user, connection_id, permission="askdata:read"):
    return lifecycle.authorized_connection(db, user, connection_id, permission)


def _configuration(db, user, connection_id, permission="askdata:read", *, lock=False):
    connection = _connection(db, user, connection_id, permission)
    row = lifecycle.repository.configuration(db, connection_id, lock=lock)
    return connection, row


def _revision(db, connection, user, row, action):
    """Bump the settings revision and audit the change on the connection."""
    if row is None:
        row = AskDataConfiguration(connection_id=connection.id)
        db.add(row)
        db.flush()
    row.version = (row.version or 0) + 1
    row.updated_by = user.id
    connection_repository.audit(db, connection, user, action)
    return row


# ---------------------------------------------------------------------------
# Execution settings
# ---------------------------------------------------------------------------


def _channels():
    from app.services import agent_config

    profile = agent_config.profile("diagnosis")
    return profile, [
        AskDataChannelOption(
            id=channel["id"],
            model=channel["model"],
            base_url=channel["base_url"],
            enabled=True,
        )
        for channel in profile["channels"]
    ]


def _execution_response(db, user, connection_id):
    connection, row = _configuration(db, user, connection_id)
    model, dimension = default_vector_settings()
    _, channels = _channels()
    return AskDataExecutionSettingsResponse(
        connection_id=connection.id,
        version=row.version if row else 0,
        budget_seconds=row.budget_seconds if row else DEFAULT_BUDGET_SECONDS,
        reasoning_channel_id=row.reasoning_channel_id if row else None,
        light_channel_id=row.light_channel_id if row else None,
        channels=channels,
        vector_model=(row.vector_model if row and row.vector_model else model),
        vector_dimension=(row.vector_dimension if row else dimension),
        vector_signature=vector_signature(
            row.vector_model if row and row.vector_model else model,
            row.vector_dimension if row else dimension,
        ),
        reranker_enabled=bool(row and row.reranker_enabled),
        reranker_endpoint=row.reranker_endpoint if row else "",
        reranker_model=row.reranker_model if row else "",
        reranker_configured=bool(row and row.reranker_key_encrypted),
        reranker_verified_at=row.reranker_verified_at if row else None,
        updated_at=row.updated_at if row else None,
    )


def execution_settings(db, user, connection_id):
    return _execution_response(db, user, connection_id)


def _validate_channel(identifier, channels):
    if identifier is None:
        return
    if str(identifier) not in {str(channel.id) for channel in channels}:
        raise HTTPException(422, "所选模型通道不可用")


def update_execution(db, user, connection_id, body: AskDataExecutionSettingsInput):
    connection, row = _configuration(db, user, connection_id, "askdata:manage", lock=True)
    current = row.version if row else 0
    if body.expected_version != current:
        raise HTTPException(409, "问数设置已变化，请刷新后重试")
    channels = _channels()[1]
    _validate_channel(body.reasoning_channel_id, channels)
    _validate_channel(body.light_channel_id, channels)

    model, dimension = default_vector_settings()
    requested_model = body.vector_model or model
    requested_dimension = body.vector_dimension or dimension
    if body.reranker_enabled:
        if row is None or not row.reranker_verified_at:
            raise HTTPException(409, "启用重排前请先通过连接测试")
        if not body.reranker_endpoint.strip():
            raise HTTPException(422, "重排服务地址不能为空")

    if row is None:
        row = AskDataConfiguration(connection_id=connection_id)
        db.add(row)
    row.budget_seconds = body.budget_seconds
    row.reasoning_channel_id = body.reasoning_channel_id
    row.light_channel_id = body.light_channel_id
    row.vector_model = requested_model
    row.vector_dimension = requested_dimension
    row.reranker_endpoint = body.reranker_endpoint.strip()
    row.reranker_model = body.reranker_model.strip()
    row.reranker_enabled = body.reranker_enabled
    if body.reranker_api_key is not None:
        row.reranker_key_encrypted = (
            cipher().encrypt(body.reranker_api_key.get_secret_value().encode()).decode()
            if body.reranker_api_key.get_secret_value()
            else None
        )
        row.reranker_verified_at = None
    _revision(db, connection, user, row, "askdata_execution_settings_updated")
    db.commit()
    db.refresh(row)
    return _execution_response(db, user, connection_id)


def test_execution(db, user, connection_id):
    connection, row = _configuration(db, user, connection_id, "askdata:manage")
    if row is None or not row.reranker_endpoint:
        raise HTTPException(409, "请先填写重排服务地址")
    service = service_databases_reranker_key(row)
    started = time.monotonic()
    try:
        _probe_reranker(row.reranker_endpoint, row.reranker_model, service)
    except Exception as exc:  # noqa: BLE001 - the message is bounded for the UI
        raise HTTPException(502, f"重排服务连接失败：{_bounded(exc)}") from exc
    row.reranker_verified_at = datetime.now(UTC)
    _revision(db, connection, user, row, "askdata_reranker_verified")
    db.commit()
    return AskDataConnectionTestResponse(
        ok=True,
        message="重排服务连接正常",
        latency_ms=int((time.monotonic() - started) * 1000),
        tested_at=row.reranker_verified_at,
    )


def service_databases_reranker_key(row):
    if not row or not row.reranker_key_encrypted:
        return ""
    return cipher().decrypt(row.reranker_key_encrypted.encode()).decode()


def _probe_reranker(endpoint, model, api_key):
    import httpx

    url = endpoint.rstrip("/")
    if not url.endswith(("/rerank", "/reranking", "/ranking")):
        url = f"{url}/v1/ranking"
    headers = {"accept": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    if url.endswith("/rerank"):
        payload = {"model": model, "query": "连接测试", "documents": ["连接测试"], "top_n": 1}
    else:
        payload = {
            "model": model,
            "query": {"text": "连接测试"},
            "passages": [{"text": "连接测试"}],
            "truncate": "END",
        }
    response = httpx.post(url, json=payload, headers=headers, timeout=10)
    response.raise_for_status()


def _bounded(exc):
    text = str(exc) or exc.__class__.__name__
    return text[:120]


def execution_context(db, connection_id):
    """Settings copied into one build or query task. Never returns secrets."""
    row = lifecycle.repository.configuration(db, connection_id)
    model, dimension = default_vector_settings()
    signature = vector_signature(
        row.vector_model if row and row.vector_model else model,
        row.vector_dimension if row else dimension,
    )
    return {
        "budget_seconds": row.budget_seconds if row else DEFAULT_BUDGET_SECONDS,
        "reasoning_channel_id": str(row.reasoning_channel_id)
        if row and row.reasoning_channel_id
        else "",
        "light_channel_id": str(row.light_channel_id) if row and row.light_channel_id else "",
        "vector_signature": signature,
        "vector_model": row.vector_model if row and row.vector_model else model,
        "vector_dimension": row.vector_dimension if row else dimension,
        "reranker": {
            "enabled": bool(row and row.reranker_enabled),
            "endpoint": row.reranker_endpoint if row and row.reranker_enabled else "",
            "model": row.reranker_model if row and row.reranker_enabled else "",
            "api_key": service_databases_reranker_key(row) if row and row.reranker_enabled else "",
        },
    }


def model_config(db, connection_id):
    """Upstream ``model_config`` mapping for one task (no secrets for rerank)."""
    context = execution_context(db, connection_id)
    mapping = {}
    if context["reasoning_channel_id"]:
        mapping["ASKDATA_REASONING_CHANNEL"] = context["reasoning_channel_id"]
    if context["light_channel_id"]:
        mapping["ASKDATA_LIGHT_CHANNEL"] = context["light_channel_id"]
    reranker = context["reranker"]
    if reranker["enabled"]:
        mapping.update(
            {
                "RERANK_ENDPOINT": reranker["endpoint"],
                "RERANK_MODEL": reranker["model"],
                "RERANK_API_KEY": reranker["api_key"],
            }
        )
    return mapping


# ---------------------------------------------------------------------------
# Sensitive fields
# ---------------------------------------------------------------------------


def sensitive_rows(db, connection_id):
    return db.scalars(
        select(AskDataSensitiveField)
        .where(
            AskDataSensitiveField.connection_id == connection_id,
            AskDataSensitiveField.deleted_at.is_(None),
            AskDataSensitiveField.enabled.is_(True),
        )
        .order_by(AskDataSensitiveField.object_key)
        .execution_options(access_internal=True)
    ).all()


def _segments(object_key):
    return [part for part in str(object_key or "").split(".") if part]


def sensitive_columns(db, connection_id):
    """Map ``schema.table.column`` to its policy, expanding table-level rows."""
    table_rules = {}
    column_rules = {}
    for row in sensitive_rows(db, connection_id):
        parts = _segments(row.object_key)
        if len(parts) >= 3:
            column_rules[".".join(parts[:3])] = row
        elif len(parts) == 2:
            table_rules[".".join(parts)] = row
    return table_rules, column_rules


def sampling_exclusions(db, connection_id, tables):
    """Columns the sampler must not read, keyed by ``schema.table``."""
    table_rules, column_rules = sensitive_columns(db, connection_id)
    excluded = {}
    for key, table in (tables or {}).items():
        blocked = set()
        table_rule = table_rules.get(key)
        if table_rule is not None and table_rule.sampling == "exclude":
            blocked.update(column.get("name") for column in table.get("columns", []))
        for column in table.get("columns", []):
            rule = column_rules.get(f"{key}.{column.get('name')}")
            if rule is not None and rule.sampling == "exclude":
                blocked.add(column.get("name"))
        if blocked:
            excluded[key] = sorted(name for name in blocked if name)
    return excluded


def masking_rules(db, connection_id):
    """Return ``(table_map, column_map)`` for result masking."""
    table_rules, column_rules = sensitive_columns(db, connection_id)
    return (
        {key: row for key, row in table_rules.items() if row.policy == "mask"},
        {key: row for key, row in column_rules.items() if row.policy == "mask"},
    )


def masking_plan(db, connection_id):
    """Column-name to masking rule, including table-level policies.

    Table-level rows expand through the connection's authorized schema cache so
    a policy never needs a live catalog read at query time.
    """
    table_rules, column_rules = masking_rules(db, connection_id)
    plan = {_segments(key)[-1]: rule for key, rule in column_rules.items()}
    if table_rules:
        connection = lifecycle.repository.connection(db, connection_id)
        tables = (connection.schema_cache or {}).get("tables", {}) if connection else {}
        for key, rule in table_rules.items():
            for column in tables.get(key, {}).get("columns", []):
                plan.setdefault(column.get("name"), rule)
    return plan


def mask_result(db, connection_id, columns, rows):
    """Mask sensitive values in one executed result.

    Columns are matched by name against configured rules. An alias that hides a
    sensitive column is still masked when the name matches; a renamed alias is
    outside what a name-based policy can prove, so the raw value stays governed
    by the connection grant and the SQL gate.
    """
    plan = masking_plan(db, connection_id)
    if not plan:
        return rows
    positions = [
        (index, plan[column.name]) for index, column in enumerate(columns) if column.name in plan
    ]
    if not positions:
        return rows
    masked = []
    for row in rows:
        values = list(row)
        for index, rule in positions:
            if index < len(values):
                values[index] = mask_value(values[index], rule)
        masked.append(values)
    return masked


def masking_specs(db, connection_id):
    """Serializable masking plan for the database connector (no ORM rows)."""
    return {
        name: {"strategy": rule.mask_strategy, "argument": int(rule.mask_argument or 0)}
        for name, rule in masking_plan(db, connection_id).items()
    }


def mask_value(value, rule):
    return apply_mask(value, rule.mask_strategy, rule.mask_argument)


def apply_mask(value, strategy, argument):
    if value is None:
        return None
    text = str(value)
    argument = int(argument or 0)
    if strategy == "keep_prefix" and argument:
        return text[:argument] + MASK_MARK
    if strategy == "keep_suffix" and argument:
        return MASK_MARK + text[-argument:]
    if strategy == "hash":
        return "sha256:" + hashlib.sha256(text.encode()).hexdigest()[:12]
    return MASK_MARK


def mask_samples(db, connection_id, tables):
    """Mask sampled values before they enter descriptions or vector rows."""
    plan = masking_plan(db, connection_id)
    if not plan:
        return tables
    for table in (tables or {}).values():
        for column in table.get("columns", []):
            rule = plan.get(column.get("name"))
            if rule is None:
                continue
            samples = column.get("sample_values")
            if isinstance(samples, list):
                column["sample_values"] = [mask_value(value, rule) for value in samples]
    return tables


# ---------------------------------------------------------------------------
# Zones, tags, rules, sensitive fields, supplements
# ---------------------------------------------------------------------------


def _clean(value, limit):
    return str(value or "").strip()[:limit]


def zones(db, user, connection_id):
    _connection(db, user, connection_id)
    rows = db.scalars(
        select(AskDataZone)
        .where(
            AskDataZone.connection_id == connection_id,
            AskDataZone.deleted_at.is_(None),
        )
        .order_by(AskDataZone.name)
        .execution_options(access_internal=True)
    ).all()
    return rows


def save_zone(db, user, connection_id, body: AskDataZoneInput, identifier=None):
    connection = _connection(db, user, connection_id, "askdata:manage")
    row = db.get(AskDataZone, identifier) if identifier else None
    if identifier and (row is None or row.deleted_at or row.connection_id != connection.id):
        raise HTTPException(404, "业务分区不存在")
    if row is None:
        row = AskDataZone(connection_id=connection.id)
        db.add(row)
    row.name = _clean(body.name, 100)
    row.description = _clean(body.description, 300)
    row.targets = [
        {"kind": _clean(item.get("kind"), 20), "key": _clean(item.get("key"), 300)}
        for item in body.targets
        if isinstance(item, dict) and item.get("key")
    ]
    row.enabled = body.enabled
    row.updated_by = user.id
    try:
        db.flush()
    except Exception as exc:  # noqa: BLE001 - unique violation is the expected path
        db.rollback()
        raise HTTPException(409, "同名业务分区已存在") from exc
    _revision(
        db, connection, user, lifecycle.repository.configuration(db, connection_id, lock=True),
        "askdata_zone_saved",
    )
    db.commit()
    db.refresh(row)
    return row


def delete_zone(db, user, connection_id, identifier):
    connection = _connection(db, user, connection_id, "askdata:manage")
    row = db.get(AskDataZone, identifier)
    if row is None or row.deleted_at or row.connection_id != connection.id:
        raise HTTPException(404, "业务分区不存在")
    row.deleted_at = row.updated_at = datetime.now(UTC)
    _revision(
        db, connection, user, lifecycle.repository.configuration(db, connection_id, lock=True),
        "askdata_zone_deleted",
    )
    db.commit()


def _tag_targets(db, tag_id):
    return db.scalars(
        select(AskDataTagTarget)
        .where(
            AskDataTagTarget.tag_id == tag_id,
            AskDataTagTarget.deleted_at.is_(None),
        )
        .order_by(AskDataTagTarget.object_kind, AskDataTagTarget.object_key)
        .execution_options(access_internal=True)
    ).all()


def tags(db, user, connection_id):
    _connection(db, user, connection_id)
    rows = db.scalars(
        select(AskDataTag)
        .where(
            AskDataTag.connection_id == connection_id,
            AskDataTag.deleted_at.is_(None),
        )
        .order_by(AskDataTag.name)
        .execution_options(access_internal=True)
    ).all()
    targets = {}
    if rows:
        for row in db.scalars(
            select(AskDataTagTarget)
            .where(
                AskDataTagTarget.tag_id.in_([item.id for item in rows]),
                AskDataTagTarget.deleted_at.is_(None),
            )
            .execution_options(access_internal=True)
        ):
            targets.setdefault(row.tag_id, []).append(
                {"object_kind": row.object_kind, "object_key": row.object_key}
            )
    return [(row, targets.get(row.id, [])) for row in rows]


def save_tag(db, user, connection_id, body: AskDataTagInput, identifier=None):
    connection = _connection(db, user, connection_id, "askdata:manage")
    row = db.get(AskDataTag, identifier) if identifier else None
    if identifier and (row is None or row.deleted_at or row.connection_id != connection.id):
        raise HTTPException(404, "标签不存在")
    if row is None:
        row = AskDataTag(connection_id=connection.id)
        db.add(row)
    row.name = _clean(body.name, 60)
    row.color = _clean(body.color, 20)
    row.description = _clean(body.description, 300)
    row.updated_by = user.id
    try:
        db.flush()
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        raise HTTPException(409, "同名标签已存在") from exc
    db.execute(
        delete(AskDataTagTarget)
        .where(AskDataTagTarget.tag_id == row.id)
        .execution_options(access_internal=True)
    )
    seen = set()
    for item in body.targets:
        key = (item.object_kind, _clean(item.object_key, 300))
        if not key[1] or key in seen:
            continue
        seen.add(key)
        db.add(
            AskDataTagTarget(
                connection_id=connection.id,
                tag_id=row.id,
                object_kind=item.object_kind,
                object_key=key[1],
            )
        )
    _revision(
        db, connection, user, lifecycle.repository.configuration(db, connection_id, lock=True),
        "askdata_tag_saved",
    )
    db.commit()
    db.refresh(row)
    return row, [
        {"object_kind": kind, "object_key": key} for kind, key in sorted(seen)
    ]


def delete_tag(db, user, connection_id, identifier):
    connection = _connection(db, user, connection_id, "askdata:manage")
    row = db.get(AskDataTag, identifier)
    if row is None or row.deleted_at or row.connection_id != connection.id:
        raise HTTPException(404, "标签不存在")
    now = datetime.now(UTC)
    row.deleted_at = row.updated_at = now
    for target in _tag_targets(db, row.id):
        target.deleted_at = target.updated_at = now
    _revision(
        db, connection, user, lifecycle.repository.configuration(db, connection_id, lock=True),
        "askdata_tag_deleted",
    )
    db.commit()


def rules(db, user, connection_id):
    _connection(db, user, connection_id)
    return db.scalars(
        select(AskDataRule)
        .where(
            AskDataRule.connection_id == connection_id,
            AskDataRule.deleted_at.is_(None),
        )
        .order_by(AskDataRule.priority, AskDataRule.name)
        .execution_options(access_internal=True)
    ).all()


def save_rule(db, user, connection_id, body: AskDataRuleInput, identifier=None):
    connection = _connection(db, user, connection_id, "askdata:manage")
    row = db.get(AskDataRule, identifier) if identifier else None
    if identifier and (row is None or row.deleted_at or row.connection_id != connection.id):
        raise HTTPException(404, "业务规则不存在")
    if row is None:
        row = AskDataRule(connection_id=connection.id)
        db.add(row)
    row.name = _clean(body.name, 120)
    row.description = _clean(body.description, 300)
    row.kind = body.kind
    row.target = _clean(body.target, 300)
    row.expression = _clean(body.expression, 2000)
    row.unit = _clean(body.unit, 30)
    row.priority = body.priority
    row.enabled = body.enabled
    row.updated_by = user.id
    try:
        db.flush()
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        raise HTTPException(409, "同名业务规则已存在") from exc
    _revision(
        db, connection, user, lifecycle.repository.configuration(db, connection_id, lock=True),
        "askdata_rule_saved",
    )
    db.commit()
    db.refresh(row)
    return row


def delete_rule(db, user, connection_id, identifier):
    connection = _connection(db, user, connection_id, "askdata:manage")
    row = db.get(AskDataRule, identifier)
    if row is None or row.deleted_at or row.connection_id != connection.id:
        raise HTTPException(404, "业务规则不存在")
    row.deleted_at = row.updated_at = datetime.now(UTC)
    _revision(
        db, connection, user, lifecycle.repository.configuration(db, connection_id, lock=True),
        "askdata_rule_deleted",
    )
    db.commit()


def sensitive_fields(db, user, connection_id):
    _connection(db, user, connection_id)
    return db.scalars(
        select(AskDataSensitiveField)
        .where(
            AskDataSensitiveField.connection_id == connection_id,
            AskDataSensitiveField.deleted_at.is_(None),
        )
        .order_by(AskDataSensitiveField.object_key)
        .execution_options(access_internal=True)
    ).all()


def save_sensitive_field(
    db, user, connection_id, body: AskDataSensitiveFieldInput, identifier=None
):
    connection = _connection(db, user, connection_id, "askdata:manage")
    row = db.get(AskDataSensitiveField, identifier) if identifier else None
    if identifier and (
        row is None or row.deleted_at or row.connection_id != connection.id
    ):
        raise HTTPException(404, "敏感字段标记不存在")
    if row is None:
        row = AskDataSensitiveField(connection_id=connection.id)
        db.add(row)
    row.object_key = _clean(body.object_key, 300).strip(".")
    if _segments(row.object_key).__len__() < 2:
        raise HTTPException(422, "字段需使用 schema.table 或 schema.table.column")
    row.category = body.category
    row.policy = body.policy
    row.mask_strategy = body.mask_strategy
    row.mask_argument = body.mask_argument
    row.sampling = body.sampling
    row.note = _clean(body.note, 300)
    row.enabled = body.enabled
    row.updated_by = user.id
    try:
        db.flush()
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        raise HTTPException(409, "该字段已存在敏感标记") from exc
    _revision(
        db, connection, user, lifecycle.repository.configuration(db, connection_id, lock=True),
        "askdata_sensitive_field_saved",
    )
    db.commit()
    db.refresh(row)
    return row


def delete_sensitive_field(db, user, connection_id, identifier):
    connection = _connection(db, user, connection_id, "askdata:manage")
    row = db.get(AskDataSensitiveField, identifier)
    if row is None or row.deleted_at or row.connection_id != connection.id:
        raise HTTPException(404, "敏感字段标记不存在")
    row.deleted_at = row.updated_at = datetime.now(UTC)
    _revision(
        db, connection, user, lifecycle.repository.configuration(db, connection_id, lock=True),
        "askdata_sensitive_field_deleted",
    )
    db.commit()


def supplements(db, user, connection_id):
    _connection(db, user, connection_id)
    return db.scalars(
        select(AskDataSupplement)
        .where(
            AskDataSupplement.connection_id == connection_id,
            AskDataSupplement.deleted_at.is_(None),
        )
        .order_by(AskDataSupplement.term)
        .execution_options(access_internal=True)
    ).all()


def save_supplement(db, user, connection_id, body: AskDataSupplementInput, identifier=None):
    connection = _connection(db, user, connection_id, "askdata:manage")
    row = db.get(AskDataSupplement, identifier) if identifier else None
    if identifier and (row is None or row.deleted_at or row.connection_id != connection.id):
        raise HTTPException(404, "补充定义不存在")
    if row is None:
        row = AskDataSupplement(connection_id=connection.id)
        db.add(row)
    row.kind = body.kind
    row.term = _clean(body.term, 120)
    row.definition = _clean(body.definition, 2000)
    row.aliases = [alias.strip() for alias in body.aliases if alias.strip()][:50]
    row.applies_to = _clean(body.applies_to, 300)
    row.enabled = body.enabled
    row.updated_by = user.id
    try:
        db.flush()
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        raise HTTPException(409, "同名补充定义已存在") from exc
    _revision(
        db, connection, user, lifecycle.repository.configuration(db, connection_id, lock=True),
        "askdata_supplement_saved",
    )
    db.commit()
    db.refresh(row)
    return row


def delete_supplement(db, user, connection_id, identifier):
    connection = _connection(db, user, connection_id, "askdata:manage")
    row = db.get(AskDataSupplement, identifier)
    if row is None or row.deleted_at or row.connection_id != connection.id:
        raise HTTPException(404, "补充定义不存在")
    row.deleted_at = row.updated_at = datetime.now(UTC)
    _revision(
        db, connection, user, lifecycle.repository.configuration(db, connection_id, lock=True),
        "askdata_supplement_deleted",
    )
    db.commit()


def business_context(db, connection_id):
    """Rules and supplements injected into the question-answering prompt."""
    rule_rows = db.scalars(
        select(AskDataRule)
        .where(
            AskDataRule.connection_id == connection_id,
            AskDataRule.deleted_at.is_(None),
            AskDataRule.enabled.is_(True),
        )
        .order_by(AskDataRule.priority, AskDataRule.name)
        .execution_options(access_internal=True)
    ).all()
    supplement_rows = db.scalars(
        select(AskDataSupplement)
        .where(
            AskDataSupplement.connection_id == connection_id,
            AskDataSupplement.deleted_at.is_(None),
            AskDataSupplement.enabled.is_(True),
        )
        .order_by(AskDataSupplement.term)
        .execution_options(access_internal=True)
    ).all()
    acronyms = [
        f"{row.term}：{row.definition[:200]}"
        for row in supplement_rows
        if row.definition
    ]
    lines = ["业务规则（发布口径）："]
    for row in rule_rows:
        scope = f"（{row.target}）" if row.target else ""
        unit = f"，单位 {row.unit}" if row.unit else ""
        lines.append(f"- {row.name}{scope}：{row.expression or row.description}{unit}")
    evidence = "\n".join(lines) if rule_rows else ""
    return {"acronyms": acronyms, "evidence": evidence}
