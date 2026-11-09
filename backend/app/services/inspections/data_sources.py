"""Reusable inspection datasets. Database credentials stay in ServiceDatabase."""

import json
import re
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from fastapi import HTTPException

from app.core.secrets import cipher
from app.models import InspectionDataSource
from app.repositories import inspections as repository
from app.repositories.notifications import audit
from app.schemas.inspections import InspectionDataSourceResponse
from app.services.inspections import sources


def response(row):
    return InspectionDataSourceResponse(
        **{
            k: getattr(row, k)
            for k in InspectionDataSourceResponse.model_fields
            if k != "headers_configured"
        },
        headers_configured=bool(row.encrypted_headers),
    )


def active(db, identifier):
    row = repository.source(db, identifier)
    if not row:
        raise HTTPException(404, "巡检数据源不存在")
    return row


def validate(db, row):
    if row.source_kind == "api" and row.source_config.get("http_connection_id"):
        from app.services.inspections import connections

        connections.effective(db, row)
    if row.application_id:
        application = repository.application(db, row.application_id)
        if not application or application.deleted_at or not application.enabled:
            raise HTTPException(409, "inspection_service_unavailable")
    if row.source_kind == "database":
        sources.validate_database(db, row)


def configure(db, body, user, identifier=None):
    row = active(db, identifier) if identifier else InspectionDataSource()
    if identifier and body.expected_version != row.version:
        raise HTTPException(409, "数据源已修改，请刷新后重试")
    old_url = row.source_config.get("url") if identifier else None
    if identifier and row.source_config.get("http_connection_id"):
        from app.services.inspections.connections import effective

        previous = effective(db, row)
        old_url = previous.source_config["url"]
        if (
            body.source_kind == "api"
            and not body.source_config.http_connection_id
            and body.source_config.url == old_url
            and body.headers is None
        ):
            # Editing a legacy shared query in the unified form keeps its encrypted
            # credentials without exposing them or changing other queries.
            row.encrypted_headers = previous.encrypted_headers
    for key, value in body.model_dump(
        mode="json", exclude={"headers", "expected_version", "application_id"}
    ).items():
        setattr(row, key, value)
    row.application_id = body.application_id
    if body.headers is not None:
        headers = {k: v.get_secret_value() for k, v in body.headers.items()}
        row.encrypted_headers = (
            cipher().encrypt(json.dumps(headers).encode()).decode() if headers else None
        )
    elif identifier and old_url != body.source_config.url:
        row.encrypted_headers = None
    if row.source_kind != "api":
        row.encrypted_headers = None
    row.version = row.version + 1 if identifier else 1
    row.last_test_at = row.last_test_success = row.last_error_code = None
    validate(db, row)
    db.add(row)
    db.flush()
    audit(
        db,
        user,
        "inspection_source_configured",
        "inspection_data_source",
        row.id,
        {"version": row.version},
    )
    db.commit()
    db.refresh(row)
    return response(row)


def apply_to_plan(db, plan):
    if not plan.data_source_id:
        return None
    row = active(db, plan.data_source_id)
    if (
        not row.enabled
        or row.environment != plan.environment
        or (row.application_id is not None and row.application_id != plan.application_id)
    ):
        raise HTTPException(409, "inspection_source_scope_unavailable")
    validate(db, row)
    from app.services.inspections.connections import effective

    view = effective(db, row)
    config = dict(view.source_config)
    if (
        plan.execution_mode != "agent"
        and config.get("path_template")
        and "{" not in config["path_template"]
    ):
        config["url"] = config["url"].rstrip("/") + "/" + config["path_template"]
        config["path_template"] = ""
    plan.source_kind, plan.source_config, plan.encrypted_headers = (
        view.source_kind,
        config,
        view.encrypted_headers,
    )
    return row


def apply_related(db, plan):
    headers = (
        json.loads(cipher().decrypt(plan.encrypted_related_headers.encode()))
        if plan.encrypted_related_headers
        else {}
    )
    values, versions = [], {}
    for config in plan.related_sources or []:
        value = dict(config)
        if value.get("data_source_id"):
            row = active(db, value["data_source_id"])
            if (
                not row.enabled
                or row.environment != plan.environment
                or (row.application_id and row.application_id != plan.application_id)
            ):
                raise HTTPException(409, "inspection_source_scope_unavailable")
            validate(db, row)
            value.update(source_kind=row.source_kind, source_config=row.source_config)
            if (
                row.source_kind == "database"
                and value["match_field"] not in row.source_config["fact_columns"]
            ):
                raise HTTPException(422, "请将关联匹配字段加入数据源的检查字段")
            headers[value["name"]] = (
                json.loads(cipher().decrypt(row.encrypted_headers.encode()))
                if row.encrypted_headers
                else {}
            )
            versions[str(row.id)] = row.version
        values.append(value)
    plan.related_sources = values
    plan.encrypted_related_headers = (
        cipher().encrypt(json.dumps(headers).encode()).decode() if any(headers.values()) else None
    )
    return versions


def test_connection(db, identifier, user):
    row = active(db, identifier)
    now = datetime.now(UTC)
    context = SimpleNamespace(
        window_start=now - timedelta(days=30), window_end=now, page_cursor={}, snapshot={}
    )
    count, error = 0, None
    try:
        validate(db, row)
        if row.source_kind == "api" and row.source_config.get("agent_api_enabled"):
            from app.schemas.inspections import InspectionAPIRequest
            from app.services.inspections.workflow import request_api

            if "{" in row.source_config.get("path_template", ""):
                raise ValueError("inspection_api_parameters_required")
            from app.services.inspections.connections import effective

            value = request_api(effective(db, row), InspectionAPIRequest(source_id=row.id))
            count = len(value) if isinstance(value, list) else 1
            row.last_test_at, row.last_test_success, row.last_error_code = now, True, None
            audit(
                db,
                user,
                "inspection_source_tested",
                "inspection_data_source",
                row.id,
                {"success": True},
            )
            db.commit()
            return {"success": True, "tested_at": now, "error_code": None, "records_read": count}
        if row.source_kind == "database":
            from app.services import service_databases

            context.snapshot["database"] = service_databases.frozen(
                sources.validate_database(db, row)
            )
        from app.services.inspections.connections import effective

        view = effective(db, row)
        if view.source_kind == "api" and view.source_config.get("path_template"):
            config = dict(view.source_config)
            if "{" in config["path_template"]:
                raise ValueError("inspection_api_parameters_required")
            config["url"] = config["url"].rstrip("/") + "/" + config["path_template"]
            config["path_template"] = ""
            view = SimpleNamespace(
                source_config=config,
                encrypted_headers=view.encrypted_headers,
                source_kind=view.source_kind,
            )
        records, _ = sources.read_page(db, view, context, 1)
        count = len(records)
    except Exception as exc:
        candidate = str(exc.detail) if isinstance(exc, HTTPException) else str(exc)
        error = (
            candidate
            if re.fullmatch(r"[a-z_]{1,80}", candidate)
            else "inspection_source_unavailable"
        )
    row.last_test_at, row.last_test_success, row.last_error_code = now, error is None, error
    audit(
        db,
        user,
        "inspection_source_tested",
        "inspection_data_source",
        row.id,
        {"success": error is None},
    )
    db.commit()
    return {"success": error is None, "tested_at": now, "error_code": error, "records_read": count}


def remove(db, identifier, user):
    row = active(db, identifier)
    if db.scalar(repository.source_references(identifier).limit(1)):
        raise HTTPException(409, "数据源仍被计划引用，请先调整关联计划")
    row.deleted_at = row.updated_at = datetime.now(UTC)
    audit(db, user, "inspection_source_deleted", "inspection_data_source", row.id)
    db.commit()


def preview(db, identifier, body, user):
    from app.schemas.inspections import InspectionSource
    from app.services import service_databases
    from app.services.inspections import connections

    source = active(db, identifier)
    version = source.version
    if not source.enabled:
        raise HTTPException(409, "inspection_source_unavailable")
    validate(db, source)
    view = connections.effective(db, source)
    config = dict(view.source_config)
    if view.source_kind == "api" and config.get("request_method", "GET") != "GET":
        raise HTTPException(422, "inspection_discovery_requires_fixed_get")
    if config.get("path_template"):
        if "{" in config["path_template"]:
            raise HTTPException(422, "inspection_discovery_requires_fixed_query")
        config["url"] = config["url"].rstrip("/") + "/" + config["path_template"]
        config["path_template"] = ""
    view = SimpleNamespace(
        source_kind=view.source_kind,
        source_config=config,
        encrypted_headers=view.encrypted_headers,
        application_id=view.application_id,
        environment=view.environment,
        execution_mode="checks",
    )
    now = datetime.now(UTC)
    context = SimpleNamespace(
        window_start=now - timedelta(days=30), window_end=now, page_cursor={}, snapshot={}
    )
    try:
        if view.source_kind == "database":
            connection = sources.validate_database(db, view)
            context.snapshot["database"] = service_databases.frozen(connection)
            if config.get("query_sql"):
                records, cursor = sources.saved_query_page(
                    InspectionSource.model_validate(config),
                    connection,
                    context,
                    body.limit,
                    parameters=body.parameters,
                )
            elif body.parameters:
                raise ValueError("inspection_query_parameters_not_authorized")
            else:
                records, cursor = sources.read_page(db, view, context, body.limit)
        else:
            if body.parameters:
                raise ValueError("inspection_preview_uses_saved_parameters")
            records, cursor = sources.read_page(db, view, context, body.limit)
        db.expire_all()
        current = active(db, identifier)
        if current.version != version or not current.enabled:
            raise ValueError("inspection_source_changed")
        connections.effective(db, current)
        if len({record.id for record in records}) != len(records):
            raise ValueError("inspection_duplicate_object")
    except HTTPException:
        raise
    except Exception as exc:
        candidate = str(exc) if isinstance(exc, ValueError) else "inspection_query_preview_failed"
        code = (
            candidate
            if re.fullmatch(r"[a-z_]{1,80}", candidate)
            else "inspection_query_preview_failed"
        )
        raise HTTPException(422, code) from None
    audit(
        db,
        user,
        "inspection_query_previewed",
        "inspection_data_source",
        source.id,
        {"records": len(records)},
    )
    db.commit()
    return {
        "records": [record.model_dump(mode="json") for record in records],
        "next_cursor": cursor,
    }
