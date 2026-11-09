"""Saved analyses: durable question, SQL and chart, replayed under live grants.

A saved analysis stores the SQL that produced its result, not only the answer.
Re-execution therefore re-validates the statement against the connection's
current authorized tables and the active model version before it runs, and the
returned rows pass the same sensitive-field masking as a conversation run.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import HTTPException
from sqlalchemy import func, select

from app.models import AskDataAnalysis
from app.repositories import askdata_runs as repository
from app.repositories import service_databases as connection_repository
from app.schemas.askdata_runs import AskDataChartSpec, AskDataColumnMeta, AskDataResult
from app.schemas.askdata_stage5 import (
    AskDataAnalysisCreate,
    AskDataAnalysisRunResponse,
    AskDataAnalysisUpdate,
)
from app.services import askdata as settings
from app.services import askdata_settings

MAX_ITEMS = 200


def _owned(db, user, identifier):
    row = db.get(AskDataAnalysis, identifier)
    if row is None or row.deleted_at or row.owner_id != user.id:
        raise HTTPException(404, "分析不存在")
    settings.authorized_connection(db, user, row.connection_id, "askdata:read")
    return row


def listing(db, user, connection_id=None, search="", category="", offset=0, limit=50):
    query = select(AskDataAnalysis).where(
        AskDataAnalysis.owner_id == user.id,
        AskDataAnalysis.deleted_at.is_(None),
    )
    if connection_id:
        settings.authorized_connection(db, user, connection_id, "askdata:read")
        query = query.where(AskDataAnalysis.connection_id == connection_id)
    if search.strip():
        pattern = f"%{search.strip()}%"
        query = query.where(
            AskDataAnalysis.name.ilike(pattern) | AskDataAnalysis.question.ilike(pattern)
        )
    if category.strip():
        query = query.where(AskDataAnalysis.category == category.strip())
    total = db.scalar(
        select(func.count()).select_from(query.subquery()).execution_options(access_internal=True)
    )
    rows = db.scalars(
        query.order_by(AskDataAnalysis.updated_at.desc(), AskDataAnalysis.id)
        .offset(offset)
        .limit(min(limit, MAX_ITEMS))
        .execution_options(access_internal=True, populate_existing=True)
    ).all()
    categories = db.scalars(
        select(AskDataAnalysis.category)
        .where(
            AskDataAnalysis.owner_id == user.id,
            AskDataAnalysis.deleted_at.is_(None),
            AskDataAnalysis.category != "",
        )
        .distinct()
        .execution_options(access_internal=True)
    ).all()
    return rows, int(total or 0), sorted(categories)


def create(db, user, body: AskDataAnalysisCreate):
    settings.authorized_connection(db, user, body.connection_id, "askdata:run")
    row = AskDataAnalysis(
        connection_id=body.connection_id,
        owner_id=user.id,
        name=body.name.strip(),
        category=body.category.strip(),
        question=body.question.strip(),
        sql_text=body.sql_text.strip(),
        explanation=body.explanation.strip(),
        chart=body.chart,
        evidence=body.evidence,
        model_version_id=body.model_version_id,
        source_run_id=body.source_run_id,
    )
    db.add(row)
    try:
        db.flush()
    except Exception as exc:  # noqa: BLE001 - the unique name is the expected path
        db.rollback()
        raise HTTPException(409, "同名分析已存在") from exc
    db.commit()
    db.refresh(row)
    return row


def detail(db, user, identifier):
    return _owned(db, user, identifier)


def update(db, user, identifier, body: AskDataAnalysisUpdate):
    row = _owned(db, user, identifier)
    settings.authorized_connection(db, user, row.connection_id, "askdata:run")
    row.name = body.name.strip()
    row.category = body.category.strip()
    row.question = body.question.strip()
    row.sql_text = body.sql_text.strip()
    row.explanation = body.explanation.strip()
    row.chart = body.chart
    row.evidence = body.evidence
    try:
        db.commit()
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        raise HTTPException(409, "同名分析已存在") from exc
    db.refresh(row)
    return row


def delete(db, user, identifier):
    row = _owned(db, user, identifier)
    row.deleted_at = row.updated_at = datetime.now(UTC)
    db.commit()


def execute(db, user, identifier):
    """Re-run the saved SQL under the current grant and active model version."""
    row = _owned(db, user, identifier)
    connection = settings.authorized_connection(db, user, row.connection_id, "askdata:run")
    version_id = repository.active_version(db, connection.id)
    if version_id is None:
        raise HTTPException(409, "该连接尚未启用业务模型，请先完成构建并启用")
    settings.validate_version(db, user, connection.id, version_id, "askdata:run")
    settings.require_active()

    connector = settings.database_connector(
        user.id, connection.id, version_id, gate="askdata"
    )
    started = datetime.now(UTC)
    try:
        raw = connector.execute_raw(row.sql_text)
    except ValueError as exc:
        code = str(exc.args[0]) if exc.args else "sql_rejected"
        _record(row, "failed", code, None, 0)
        connection_repository.audit(db, connection, user, "askdata_analysis_failed")
        db.commit()
        raise HTTPException(422, f"查询未执行：{code}") from exc
    except Exception as exc:  # noqa: BLE001
        _record(row, "failed", "query_execution_failed", None, 0)
        connection_repository.audit(db, connection, user, "askdata_analysis_failed")
        db.commit()
        raise HTTPException(502, "查询执行失败") from exc

    columns = [AskDataColumnMeta.model_validate(item) for item in (raw.get("column_types") or [])]
    if not columns:
        columns = [AskDataColumnMeta(name=name, kind="text") for name in raw.get("columns", [])]
    rows = askdata_settings.mask_result(db, connection.id, columns, raw.get("rows", []))
    duration = int((datetime.now(UTC) - started).total_seconds() * 1000)
    chart = AskDataChartSpec.model_validate(row.chart) if row.chart else None
    if chart is None or not chart.y:
        chart = chart_for(columns, rows)
    result = AskDataResult(
        answer=row.explanation,
        sql=row.sql_text,
        executed_sql=raw.get("executed_sql"),
        columns=columns,
        rows=rows,
        chart=chart,
        evidence=row.evidence or [],
        truncated=bool(raw.get("truncated")),
        query_time=raw.get("observed_at"),
        row_count=len(rows),
        certification="draft",
    )
    _record(row, "completed", None, len(rows), duration, version_id)
    connection_repository.audit(db, connection, user, "askdata_analysis_executed")
    db.commit()
    return AskDataAnalysisRunResponse(
        status="completed",
        duration_ms=duration,
        executed_at=row.last_executed_at,
        model_version_id=version_id,
        result=result,
    )


def _record(row, status, error_code, row_count, duration_ms, version_id=None):
    row.last_status = status
    row.last_error_code = error_code
    row.last_row_count = row_count
    row.last_duration_ms = duration_ms
    row.last_executed_at = datetime.now(UTC)
    if version_id is not None:
        row.model_version_id = version_id


def chart_for(columns, rows):
    from app.services.askdata_query import chart_for as build

    return build(columns, rows)
