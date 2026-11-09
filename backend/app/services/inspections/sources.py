"""Fixed GET or generated SELECT; remote data cannot select tools or endpoints."""

import asyncio
import json
import time
from datetime import UTC, datetime
from types import SimpleNamespace

import httpx2
from fastapi import HTTPException
from sqlglot import exp

from app.core.secrets import cipher
from app.integrations import database_readonly as adapter
from app.integrations.database_sql import checked_query
from app.models import Application
from app.schemas.inspections import InspectionRecord, InspectionSource
from app.services import service_databases
from app.utils.redaction import redact


def validate_database(db, plan):
    source = InspectionSource.model_validate(plan.source_config)
    row = service_databases.active(db, source.connection_id)
    owner = db.get(Application, row.application_id) if row.application_id else None
    if (
        (
            row.application_id is not None
            and plan.application_id is not None
            and row.application_id != plan.application_id
        )
        or row.environment != plan.environment
        or not row.enabled
        or not row.query_enabled
        or row.permission_status != "verified"
        or (row.application_id is not None and (not owner or not owner.enabled))
    ):
        raise HTTPException(409, "inspection_database_scope_unavailable")
    if source.query_sql:
        from app.services.inspections import queries

        try:
            queries.validate(source, row)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        return row
    columns = [source.id_column, *source.fact_columns]
    columns.extend(
        c for c in (source.version_column, source.time_column, source.decision_column) if c
    )
    if not set(columns) <= set(row.allowed_tables.get(source.table, [])):
        raise HTTPException(422, "inspection_columns_not_authorized")
    if source.table not in row.schema_cache.get("tables", {}):
        raise HTTPException(422, "inspection_schema_unavailable")
    known = {c["name"] for c in row.schema_cache["tables"][source.table]["columns"]}
    if not set(columns) <= known:
        raise HTTPException(422, "inspection_schema_unavailable")
    return row


async def api_page(plan, run, limit, lookup=None):
    source = InspectionSource.model_validate(plan.source_config)
    params = {
        "updated_after": run.window_start.isoformat(),
        "updated_before": run.window_end.isoformat(),
        "limit": limit,
    }
    if lookup is not None:
        params = {"match_field": lookup[0], "match_value": lookup[1], "limit": limit}
    if source.response_format == "json":
        params = {}
        if source.pagination == "cursor" and run.page_cursor:
            params[source.page_parameter] = run.page_cursor["value"]
        elif source.pagination in {"page", "offset"}:
            params[source.page_parameter] = run.page_cursor.get(
                "value", 1 if source.pagination == "page" else 0
            )
        if source.pagination != "none":
            params[source.size_parameter] = limit
    elif run.page_cursor:
        params["cursor"] = run.page_cursor["value"]
    request_params = [(p.name, p.value) for p in source.query_params] + list(params.items())
    headers = (
        json.loads(cipher().decrypt(plan.encrypted_headers.encode()))
        if plan.encrypted_headers
        else {}
    )
    started = time.monotonic()
    async with (
        asyncio.timeout(10),
        httpx2.AsyncClient(timeout=10, follow_redirects=False, trust_env=False) as client,
    ):
        async with client.stream(
            "GET", source.url, params=request_params, headers=headers
        ) as response:
            if response.status_code != 200 and not (
                source.response_format == "json"
                and getattr(plan, "execution_mode", None) == "checks"
            ):
                raise ValueError("source_http_error")
            raw = bytearray()
            async for chunk in response.aiter_bytes():
                raw.extend(chunk)
                if len(raw) > 2 * 1024 * 1024:
                    raise ValueError("source_response_too_large")
    http = {
        "status_code": response.status_code,
        "elapsed_ms": round((time.monotonic() - started) * 1000, 3),
    }
    try:
        data = json.loads(raw)
    except (ValueError, UnicodeError):
        if source.response_format != "json" or source.items_path:
            raise ValueError("source_invalid_json") from None
        data = {"body": raw.decode(errors="replace")[:4096]}
    secret_values = set(headers.values())
    for name, value in headers.items():
        if name.lower() == "authorization":
            scheme, _, credential = value.partition(" ")
            if scheme.lower() == "bearer":
                secret_values.add(credential)
            elif scheme.lower() == "basic":
                import base64
                import binascii

                try:
                    decoded = base64.b64decode(credential, validate=True).decode()
                    secret_values.update([decoded, decoded.partition(":")[2]])
                except (ValueError, UnicodeError, binascii.Error):
                    pass

    def clean(value):
        if isinstance(value, dict):
            return {key: clean(item) for key, item in value.items()}
        if isinstance(value, list):
            return [clean(item) for item in value]
        if isinstance(value, str):
            for secret in secret_values:
                if secret:
                    value = value.replace(secret, "[REDACTED]")
        return value

    data = redact(clean(data))
    if source.response_format == "json":
        from app.services.inspections.checkers import field

        payload = field(data, source.items_path) if source.items_path else data
        objects = payload if isinstance(payload, list) else [payload]
        if not all(isinstance(obj, dict) for obj in objects):
            raise ValueError("source_invalid_objects")
        if lookup:
            objects = [obj for obj in objects if str(field(obj, lookup[0])) == str(lookup[1])]
        offset = run.page_cursor.get("offset", 0) if source.pagination == "none" else 0
        records = []
        for index, obj in enumerate(objects[offset : offset + limit], offset):
            try:
                identifier = field(obj, source.object_id_path)
            except (KeyError, TypeError, IndexError):
                if getattr(plan, "execution_mode", None) == "checks" and isinstance(payload, list):
                    raise ValueError("source_object_id_required") from None
                identifier = "response" if len(objects) == 1 else str(index + 1)
            if getattr(plan, "execution_mode", None) == "checks" and (
                not isinstance(identifier, (str, int))
                or isinstance(identifier, bool)
                or not str(identifier)
            ):
                raise ValueError("source_object_id_required")
            records.append(
                InspectionRecord(
                    id=str(identifier),
                    version=service_databases.digest(obj),
                    updated_at=datetime.now(UTC),
                    facts={**obj, "_http": http}
                    if getattr(plan, "execution_mode", None) == "checks"
                    else obj,
                    evidence=[
                        {
                            "source": "api",
                            "url": source.url,
                            "observed_at": datetime.now(UTC).isoformat(),
                        }
                    ],
                )
            )
        if source.pagination == "none":
            return records, {"offset": offset + limit} if offset + limit < len(objects) else {}
        if len(objects) > limit:
            raise ValueError("source_pagination_invalid")
        if source.pagination == "cursor":
            cursor = field(data, source.next_cursor_path)
            if cursor is None or cursor == "":
                return records, {}
            if not isinstance(cursor, (str, int)) or isinstance(cursor, bool):
                raise ValueError("source_invalid_cursor")
            cursor = {"value": str(cursor)}
        else:
            if source.has_more_path:
                more = field(data, source.has_more_path)
                if not isinstance(more, bool):
                    raise ValueError("source_invalid_page")
            else:
                more = len(objects) == limit
            if not more:
                return records, {}
            cursor = {
                "value": params[source.page_parameter]
                + (1 if source.pagination == "page" else limit)
            }
        if not records or cursor == run.page_cursor:
            raise ValueError("source_pagination_invalid")
        return records, cursor
    if not isinstance(data, dict) or not isinstance(data.get("items"), list):
        raise ValueError("source_invalid_page")
    records = [InspectionRecord.model_validate(item) for item in data["items"]]
    cursor = data.get("next_cursor")
    if cursor is not None and (not isinstance(cursor, str) or not cursor or len(cursor) > 2000):
        raise ValueError("source_invalid_cursor")
    if len(records) > limit or (cursor and (not records or cursor == params.get("cursor"))):
        raise ValueError("source_pagination_invalid")
    return records, {"value": cursor} if cursor else {}


def database_page(db, plan, run, limit, lookup=None):
    source = InspectionSource.model_validate(plan.source_config)
    row = validate_database(db, plan)
    if service_databases.frozen(row) != run.snapshot["database"]:
        raise ValueError("database_configuration_changed")
    if source.query_sql:
        return saved_query_page(source, row, run, limit, lookup)
    columns = list(
        dict.fromkeys(
            [
                c
                for c in [
                    source.id_column,
                    source.version_column,
                    source.time_column,
                    *source.fact_columns,
                    *([source.decision_column] if source.decision_column else []),
                ]
                if c
            ]
        )
    )
    schema, table = source.table.split(".")

    def col(name):
        return exp.column(name, quoted=True)

    timestamp = col(source.time_column) if source.time_column else None

    def time_literal(value):
        value = value.astimezone(UTC)
        formatted = (
            value.strftime("%Y-%m-%d %H:%M:%S.%f") if row.engine == "mysql" else value.isoformat()
        )
        return exp.Literal.string(formatted)

    conditions = (
        exp.and_(
            exp.GTE(this=timestamp.copy(), expression=time_literal(run.window_start)),
            exp.LT(this=timestamp.copy(), expression=time_literal(run.window_end)),
        )
        if timestamp is not None
        else exp.true()
    )
    if lookup is not None:
        if lookup[0] not in {source.id_column, *source.fact_columns}:
            raise ValueError("lookup_column_not_authorized")
        conditions = exp.EQ(this=col(lookup[0]), expression=exp.Literal.string(lookup[1]))
    elif run.page_cursor and timestamp is None:
        conditions = exp.GT(
            this=col(source.id_column), expression=exp.Literal.string(run.page_cursor["id"])
        )
    elif run.page_cursor:
        after = run.page_cursor
        conditions = exp.and_(
            conditions,
            exp.or_(
                exp.GT(
                    this=timestamp.copy(),
                    expression=time_literal(datetime.fromisoformat(after["time"])),
                ),
                exp.and_(
                    exp.EQ(
                        this=timestamp.copy(),
                        expression=time_literal(datetime.fromisoformat(after["time"])),
                    ),
                    exp.GT(this=col(source.id_column), expression=exp.Literal.string(after["id"])),
                ),
            ),
        )
    query = (
        exp.select(*[col(c) for c in columns])
        .from_(
            exp.Table(
                this=exp.to_identifier(table, quoted=True),
                db=exp.to_identifier(schema, quoted=True),
            )
        )
        .where(conditions)
        .order_by(*([timestamp.copy()] if timestamp is not None else []), col(source.id_column))
        .limit(limit)
    )
    sql, _, _ = checked_query(
        query.sql(dialect="postgres" if row.engine == "postgresql" else "mysql"),
        row.engine,
        row.allowed_tables,
        row.schema_cache["tables"],
    )
    with service_databases.connection_lock(row.id), adapter.connect(row) as connection:
        if adapter.permissions(row, connection) != "verified":
            raise ValueError("database_permissions_unverified")
        result = adapter.query(connection, sql)
    if result["truncated"]:
        raise ValueError("source_result_truncated")
    records = []
    for cells in result["rows"]:
        item = dict(zip(result["columns"], cells, strict=True))
        raw_verdict = item.get(source.decision_column)
        verdict = (
            "pass"
            if raw_verdict == source.decision_pass_value
            else ("fail" if raw_verdict == source.decision_fail_value else "unknown")
        )
        observed_time = datetime.fromisoformat(
            item[source.time_column] if source.time_column else result["observed_at"]
        )
        if observed_time.tzinfo is None:
            column_type = next(
                (
                    c.get("type")
                    for c in row.schema_cache["tables"][source.table]["columns"]
                    if c["name"] == source.time_column
                ),
                "",
            )
            if not source.naive_time_is_utc and not (
                row.engine == "mysql" and column_type == "timestamp"
            ):
                raise ValueError("source_timezone_required")
            observed_time = observed_time.replace(tzinfo=UTC)
        records.append(
            InspectionRecord(
                id=item[source.id_column],
                version=item[source.version_column]
                if source.version_column
                else service_databases.digest(item),
                updated_at=observed_time,
                facts={key: item[key] for key in source.fact_columns},
                original_verdict=verdict,
                evidence=[
                    {
                        "source": "database",
                        "connection_id": str(row.id),
                        "table": source.table,
                        "observed_at": result["observed_at"],
                        "time_basis": "source_column" if source.time_column else "query_time",
                        "version_basis": "source_column"
                        if source.version_column
                        else "content_hash",
                    }
                ],
            )
        )
    cursor = (
        {"time": records[-1].updated_at.isoformat(), "id": records[-1].id}
        if records and len(records) == limit
        else {}
    )
    return records, cursor


def saved_query_page(source, row, run, limit, lookup=None, parameters=None):
    from app.services.inspections import queries

    tree, identity = queries.validate(source, row, parameters)
    if lookup:
        if lookup[0] not in {source.id_column, *source.fact_columns}:
            raise ValueError("lookup_column_not_authorized")
        tree = tree.where(exp.EQ(this=exp.column(lookup[0]), expression=exp.convert(lookup[1])))
    elif run.page_cursor:
        tree = tree.where(
            exp.GT(this=identity.copy(), expression=exp.convert(run.page_cursor["id"]))
        )
    tree = tree.order_by(identity.copy(), append=False).limit(min(limit + 1, 200))
    sql, _, _ = checked_query(
        tree.sql(dialect="postgres" if row.engine == "postgresql" else "mysql"),
        row.engine,
        row.allowed_tables,
        row.schema_cache["tables"],
    )
    with service_databases.connection_lock(row.id), adapter.connect(row) as connection:
        if adapter.permissions(row, connection) != "verified":
            raise ValueError("database_permissions_unverified")
        result = adapter.query(connection, sql)
    if result["truncated"]:
        raise ValueError("source_result_truncated")
    rows = [dict(zip(result["columns"], cells, strict=True)) for cells in result["rows"]]
    records = [
        InspectionRecord(
            id=str(item[source.id_column]),
            version=service_databases.digest(item),
            updated_at=datetime.fromisoformat(result["observed_at"]),
            facts={key: item[key] for key in source.fact_columns},
            evidence=[
                {
                    "source": "database",
                    "connection_id": str(row.id),
                    "observed_at": result["observed_at"],
                    "time_basis": "query_time",
                }
            ],
        )
        for item in rows[:limit]
    ]
    return records, {"id": records[-1].id} if len(rows) > limit else {}


def related_view(plan, source):
    headers = (
        json.loads(cipher().decrypt(plan.encrypted_related_headers.encode()))
        if plan.encrypted_related_headers
        else {}
    )
    own_headers = headers.get(source["name"], {})
    return SimpleNamespace(
        application_id=plan.application_id,
        environment=plan.environment,
        source_kind=source["source_kind"],
        source_config=source["source_config"],
        encrypted_headers=cipher().encrypt(json.dumps(own_headers).encode()).decode()
        if own_headers
        else None,
    )


def lookup_records(db, plan, run, source, key):
    view = related_view(plan, source)
    cap = 1 if source["cardinality"] == "one" else source["max_records"]
    lookup = (source["match_field"], key)
    context = SimpleNamespace(
        window_start=run.window_start,
        window_end=run.window_end,
        page_cursor={},
        snapshot={"database": run.snapshot.get("related_databases", {}).get(source["name"])},
    )
    if view.source_kind == "database":
        records, _ = database_page(db, view, context, cap + 1, lookup)
        return records[:cap], len(records) > cap

    async def fetch():
        records, visited = [], set()
        async with asyncio.timeout(10):
            while True:
                batch, cursor = await api_page(
                    view, context, min(50, cap + 1 - len(records)), lookup
                )
                records.extend(batch)
                if len(records) > cap:
                    return records[:cap], True
                if not cursor:
                    return records, False
                if len(records) == cap:
                    return records, True
                if cursor["value"] in visited:
                    raise ValueError("source_pagination_invalid")
                visited.add(cursor["value"])
                context.page_cursor = cursor

    return asyncio.run(fetch())


def read_page(db, plan, run, limit):
    try:
        records, cursor = READERS[plan.source_kind](db, plan, run, limit)
    except TimeoutError:
        raise ValueError("source_timeout") from None
    source = InspectionSource.model_validate(plan.source_config)
    for record in records:
        if (
            (plan.source_kind == "api" and source.response_format == "records")
            or source.time_column
        ) and not run.window_start <= record.updated_at < run.window_end:
            raise ValueError("source_record_outside_window")
    return records, cursor


# A new read-only adapter implements (db, plan, run, limit) -> (records, cursor).
READERS = {
    "api": lambda db, plan, run, limit: asyncio.run(api_page(plan, run, limit)),
    "database": database_page,
}
